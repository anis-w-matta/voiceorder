import logging
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings
from app.schemas.transcript import Transcript
from app.services.audio_duration import duration_seconds
from app.services.audio_formats import AUDIO_MIME_BY_EXT as _MIME_BY_EXT
from app.services.gemini_retry import gemini_retry
from app.services.normalization import normalize_text
from app.services.rate_limiter import gemini_rate_limiter
from app.services.silence_trim import trim_silence
from app.services.transcript_quality import assess_quality, compare_transcripts

_log = logging.getLogger(__name__)

PROMPT = """Transcribe this phone voice message verbatim.

The speaker may mix Arabic and English, sometimes within one sentence.
Write everything in Latin letters, one consistent script throughout -
English words as normal English, and any Arabic speech transliterated
into Arabizi the way Lebanese speakers actually type it: standard Latin
letters, with digits standing in for the sounds Latin letters don't cover
(3=ع, 7=ح, 2=ء/ق, 5=خ, 8/6=ط, 9=ص). E.g. "بدي" -> "baddi", "شو" -> "shu",
"كيفك" -> "kifak", "3andkon" for عندكن. Do not use Arabic script anywhere,
even for a single word. Do not translate the meaning - only change the
script Arabic is written in; English words stay in English. Preserve
product names, brands, SKU-like codes, numbers, quantities, units, colors,
sizes, discounts, and promotion codes exactly as spoken - keep an English
or French business term in its original spelling if that is what was said,
never translate or "correct" it into a different word. Do not summarize,
correct, or add anything that was not said. Do not invent a word to make
the sentence more grammatical, and do not substitute a product name for a
different one you think makes more sense. If part of the audio is
inaudible or silent, skip it rather than guessing.

Also rate your own confidence in this transcript from 0.0 to 1.0, based on
how clearly you could actually hear the speech - not on whether the
content makes sense. Lower it for background noise, mumbling, crosstalk,
a bad connection, or any word/phrase you had to guess at rather than
clearly hear. 1.0 means every word was unambiguous; below 0.5 means large
parts were a guess.

Return only the transcript and confidence in the requested format - no
explanations."""


class _GeminiTranscript(BaseModel):
    text: str
    languages: list[str] = Field(description=(
        "lowercase ISO 639-1 codes of the languages actually heard, e.g. "
        '["en"], ["ar"], or ["en","ar"] if mixed - never a language name'))
    confidence: float = Field(description=(
        "0.0-1.0 self-rated confidence in how clearly the speech was "
        "heard, per the prompt instructions - not a measure of whether "
        "the content makes sense"))


class GeminiTranscriber:
    """SpeechToText backed by Gemini's audio understanding - implements the
    transcribe(audio_path) -> Transcript protocol in app/services/protocols.py.

    Gemini exposes no per-segment timestamps or acoustic logprobs, so
    segments is always empty. confidence is the model's own 0.0-1.0
    self-rating of how clearly it heard the audio (see PROMPT) rather than
    an acoustic score - it's a judgment call, not a measurement, so
    transcribe() also runs a deterministic quality check
    (transcript_quality.assess_quality) rather than trusting this value
    alone.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.model = model or settings.gemini_model

    @gemini_retry()
    def _generate_once(self, uploaded, temperature: float) -> _GeminiTranscript:
        # Rate limit inside the retry decorator, so every individual retry
        # attempt also respects the shared RPM budget, not just the first
        # call.
        with gemini_rate_limiter:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=[uploaded, PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_GeminiTranscript,
                    temperature=temperature,
                ),
            )
        if isinstance(resp.parsed, _GeminiTranscript):
            return resp.parsed
        if resp.parsed is not None:
            return _GeminiTranscript.model_validate(resp.parsed)
        return _GeminiTranscript.model_validate_json(resp.text)

    def transcribe(self, audio_path: str, duration: float | None = None
                  ) -> Transcript:
        """`duration`: the caller's already-computed duration_seconds(audio_path),
        if it has one (IntakePipeline.process always does - it needs the
        duration up front for the too-long check before transcription even
        starts) - passing it here avoids a second full decode of the same
        file. None (the default) preserves the old self-contained behavior
        for callers that don't already have it (transcribe_file.py, ad hoc
        scripts).
        """
        ext = Path(audio_path).suffix.lower()
        if duration is None:
            duration = duration_seconds(audio_path)

        # Trimming re-encodes to WAV, so the mime type only matches the
        # original extension when trim_silence decided there was nothing
        # worth cutting and handed the input path straight back.
        with trim_silence(audio_path) as (upload_path, trimmed_duration):
            mime_type = ("audio/wav" if upload_path != audio_path
                        else _MIME_BY_EXT.get(ext, "audio/ogg"))
            uploaded = self.client.files.upload(
                file=upload_path, config={"mime_type": mime_type})
            # Quality is scored against what Gemini actually heard, not the
            # original file - using pre-trim duration here would understate
            # chars-per-second for a quiet-but-complete recording and flag
            # it questionable for no real reason.
            quality_duration = (trimmed_duration if trimmed_duration is not None
                               else duration)

            try:
                attempts: list[dict] = []
                # Do not transcribe every file twice - that wastes free
                # quota. Only retry (up to max_transcription_attempts times)
                # when the previous attempt was actually questionable. Each
                # retry nudges the temperature up a step; generated instead
                # of hardcoded so a configured attempt count above 2 isn't
                # silently truncated back down to (0, 0.2).
                attempt_count = max(1, settings.max_transcription_attempts)
                temperatures = tuple(round(i * 0.2, 2) for i in range(attempt_count))
                for temperature in temperatures:
                    try:
                        result = self._generate_once(uploaded, temperature)
                    except Exception:
                        if attempts:
                            # Keep what we already have rather than losing
                            # it to a transient failure on a retry attempt.
                            # Logged (not re-raised) so a *permanent* bug on
                            # the retry path - e.g. a pydantic
                            # ValidationError from a malformed response,
                            # which gemini_retry deliberately never retries
                            # - stays visible instead of silently vanishing
                            # behind a successful first attempt.
                            _log.warning(
                                "transcription retry attempt failed after "
                                "an earlier attempt already succeeded; "
                                "keeping the earlier result", exc_info=True)
                            break
                        raise  # nothing to salvage on the very first call
                    quality = assess_quality(result.text, quality_duration,
                                             result.confidence)
                    attempts.append({
                        "text": result.text, "confidence": result.confidence,
                        "languages": result.languages,
                        "quality": quality, "temperature": temperature})
                    if quality == "good":
                        break
            finally:
                # Best-effort cleanup only: a failure here (network blip,
                # file already GC'd server-side) must never override a
                # result or exception already in flight from the try block
                # above - losing an already-successful transcript to a
                # delete-API hiccup would be strictly worse than a leaked
                # upload.
                try:
                    self.client.files.delete(name=uploaded.name)
                except Exception:
                    _log.warning("failed to delete uploaded Gemini file %s",
                                uploaded.name, exc_info=True)

        return _build_transcript(attempts, duration)


def _build_transcript(attempts: list[dict], duration: float) -> Transcript:
    best = attempts[-1]
    disagreement = False
    if len(attempts) > 1:
        cmp = compare_transcripts(attempts[0]["text"], attempts[-1]["text"])
        if cmp.materially_different:
            disagreement = True
            # Never discard either attempt - keep the best available:
            # prefer a "good"-quality one, else the higher self-reported
            # confidence, else the first attempt made.
            good = [a for a in attempts if a["quality"] == "good"]
            if good:
                best = good[0]
            else:
                best = max(attempts, key=lambda a: a["confidence"])

    languages = best["languages"] or ["unknown"]
    return Transcript(
        text=best["text"],
        normalized_transcript=normalize_text(best["text"]),
        quality=best["quality"],
        disagreement=disagreement,
        attempts=attempts,
        language=languages[0],
        languages=languages,
        duration=duration,
        confidence=best["confidence"],
        segments=[],
    )
