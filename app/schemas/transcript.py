from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    avg_logprob: float


class Transcript(BaseModel):
    text: str
    # Derived via normalization.normalize_text(text) for downstream
    # comparisons only - text itself always stays exactly what Gemini
    # returned (see the transcription prompt's "faithful representation"
    # requirement).
    normalized_transcript: str = ""
    quality: str = "good"
    disagreement: bool = False
    # Audit trail of every transcription attempt made (text/confidence/
    # quality/temperature) - at most max_transcription_attempts entries.
    attempts: list[dict] = []
    language: str
    languages: list[str] = []
    duration: float
    confidence: float
    segments: list[TranscriptSegment] = []

    @property
    def is_mixed(self) -> bool:
        return len(self.languages) > 1

    @property
    def has_arabic(self) -> bool:
        return "ar" in self.languages or \
               any("؀" <= ch <= "ۿ" for ch in self.text)
