from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    avg_logprob: float


class Transcript(BaseModel):
    text: str
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
