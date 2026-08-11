import threading
from collections import Counter
from statistics import mean

from faster_whisper import WhisperModel

from app.config import settings
from app.schemas.transcript import Transcript, TranscriptSegment


class WhisperTranscriber:
    def __init__(self, model_size=None, device=None, compute_type=None,
                 catalogue_prompt: str = ""):
        self.model = WhisperModel(
            model_size or settings.whisper_model,
            device=device or settings.whisper_device,
            compute_type=compute_type or settings.whisper_compute,
        )
        self.prompt = catalogue_prompt
        self._lock = threading.Lock()

    def transcribe(self, audio_path: str) -> Transcript:
        with self._lock:
            segments, info = self.model.transcribe(
                audio_path,
                language=None,                     # never force
                multilingual=True,                 # per-segment switching
                condition_on_previous_text=False,  # no language carry-over
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 700},
                initial_prompt=self.prompt or None,
                beam_size=settings.whisper_beam_size,
            )
            segs = list(segments)          # generator: must stay in the lock

        out = [TranscriptSegment(start=s.start, end=s.end, text=s.text.strip(),
                                 avg_logprob=s.avg_logprob) for s in segs]
        text = " ".join(s.text for s in out).strip()
        conf = mean(s.avg_logprob for s in out) if out else -10.0

        langs = Counter()
        if info.language:
            langs[info.language] += 1
        if any("؀" <= ch <= "ۿ" for ch in text):
            langs["ar"] += 1

        return Transcript(text=text, language=info.language or "unknown",
                          languages=sorted(langs), duration=info.duration,
                          confidence=conf, segments=out)


def build_catalogue_prompt(session) -> str:
    from sqlalchemy import select
    from app.models import Item, ItemAlias
    descs = session.scalars(select(Item.item_desc).limit(40)).all()
    aliases = session.scalars(
        select(ItemAlias.alias).where(ItemAlias.lang != "ar").limit(40)).all()
    return ("Order, invoice, bill, cancel, repeat, quantity, box, carton, "
            "piece, talab, fatoura. Products: "
            + ", ".join(list(descs) + list(aliases)))
