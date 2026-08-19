import contextlib
import os
import tempfile
import wave

import av
import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_LEN = SAMPLE_RATE * FRAME_MS // 1000
SILENCE_DBFS = -40.0       # frames quieter than this count as silence
MIN_SILENCE_MS = 700       # only cut silence runs at least this long...
PAD_MS = 200                # ...and always leave this much of it at each edge
MIN_SAVINGS_SEC = 1.0       # skip the round-trip if there's nothing worth cutting
MIN_KEPT_FRACTION = 0.3     # refuse to cut if it would remove more than 70% of
                            # the audio - more likely a bad threshold than real
                            # silence, and losing real speech is worse than a
                            # missed cost saving


def _decode_mono16k(path: str) -> np.ndarray:
    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks = []
    with av.open(path, mode="r", metadata_errors="ignore") as container:
        try:
            frames = list(container.decode(audio=0))
        except av.error.InvalidDataError:
            frames = []
        for frame in frames + [None]:
            for rframe in resampler.resample(frame):
                chunks.append(rframe.to_ndarray())
    if not chunks:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate([c.reshape(-1) for c in chunks]).astype(np.int16)


def _speech_mask(samples: np.ndarray) -> np.ndarray:
    n_frames = len(samples) // FRAME_LEN
    if n_frames == 0:
        return np.ones(0, dtype=bool)
    blocks = samples[:n_frames * FRAME_LEN].reshape(n_frames, FRAME_LEN)
    rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1)) + 1e-9
    dbfs = 20 * np.log10(rms / 32768.0)
    return dbfs > SILENCE_DBFS


def _cut_ranges(mask: np.ndarray) -> list[tuple[int, int]]:
    """Frame-index ranges to remove: silence runs >= MIN_SILENCE_MS, each
    shrunk by PAD_MS on both sides so a cut never lands right against speech."""
    min_len = max(1, round(MIN_SILENCE_MS / FRAME_MS))
    pad = max(0, round(PAD_MS / FRAME_MS))
    cuts = []
    start = None
    for i, is_speech in enumerate(mask):
        if not is_speech and start is None:
            start = i
        elif is_speech and start is not None:
            if i - start >= min_len:
                cuts.append((start + pad, i - pad))
            start = None
    if start is not None and len(mask) - start >= min_len:
        cuts.append((start + pad, len(mask) - pad))
    return [(a, b) for a, b in cuts if b > a]


def _apply_cuts(samples: np.ndarray, cuts: list[tuple[int, int]]) -> np.ndarray:
    if not cuts:
        return samples
    kept, pos = [], 0
    for a, b in cuts:
        a, b = a * FRAME_LEN, min(b * FRAME_LEN, len(samples))
        if a > pos:
            kept.append(samples[pos:a])
        pos = max(pos, b)
    if pos < len(samples):
        kept.append(samples[pos:])
    return np.concatenate(kept) if kept else samples[:0]


@contextlib.contextmanager
def trim_silence(input_path: str):
    """Yields (path, duration_sec) to transcribe: a temp WAV with long
    silences cut out and its resulting duration, or (input_path, None) if
    there's nothing worth cutting (or trimming looks unsafe) - None tells
    the caller the original duration is still accurate. Gemini bills audio
    by duration, so removing dead air before upload directly reduces cost
    with no effect on what was actually said.
    """
    samples = _decode_mono16k(input_path)
    if len(samples) == 0:
        yield input_path, None
        return

    cuts = _cut_ranges(_speech_mask(samples))
    kept = _apply_cuts(samples, cuts)

    removed_sec = (len(samples) - len(kept)) / SAMPLE_RATE
    kept_fraction = len(kept) / len(samples)
    if removed_sec < MIN_SAVINGS_SEC or kept_fraction < MIN_KEPT_FRACTION:
        yield input_path, None
        return

    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with wave.open(tmp_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(kept.tobytes())
        yield tmp_path, len(kept) / SAMPLE_RATE
    finally:
        os.remove(tmp_path)
