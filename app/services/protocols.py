from typing import Protocol

from app.schemas.extraction import Extraction
from app.schemas.transcript import Transcript


class SpeechToText(Protocol):
    def transcribe(self, audio_path: str) -> Transcript: ...


class Classifier(Protocol):
    def classify(self, text: str) -> Extraction: ...
