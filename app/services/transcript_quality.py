import difflib
import re
from dataclasses import dataclass
from typing import Literal

from app.config import settings
from app.services.normalization import NUMBER_RE as _NUMBER_RE

TranscriptQuality = Literal["good", "questionable", "bad"]

_REPEATED_CHAR_RE = re.compile(r"(.)\1{5,}")


def assess_quality(text: str, duration: float, gemini_confidence: float
                    ) -> TranscriptQuality:
    """Deterministic categorical state - never a fabricated precise score.
    Gemini's self-reported confidence is one contributing signal among
    several, not ground truth (see the docstring in gemini_transcriber.py).
    """
    stripped = (text or "").strip()
    if not stripped:
        return "bad"
    if _REPEATED_CHAR_RE.search(stripped):
        return "bad"
    non_word = sum(1 for ch in stripped if not ch.isalnum() and not ch.isspace())
    if len(stripped) > 0 and non_word / len(stripped) > 0.4:
        return "bad"

    questionable = False
    if duration > 3.0:
        chars_per_second = len(stripped) / duration
        if chars_per_second < settings.transcript_min_chars_per_second:
            questionable = True

    tokens = stripped.split()
    if len(tokens) >= 4:
        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        if max(counts.values()) / len(tokens) > settings.transcript_repetition_threshold:
            questionable = True

    if gemini_confidence < settings.transcript_conf_min:
        questionable = True

    return "questionable" if questionable else "good"


@dataclass
class ComparisonResult:
    materially_different: bool
    token_overlap: float
    edit_ratio: float
    numeric_mismatch: bool
    reason: str


def compare_transcripts(a: str, b: str) -> ComparisonResult:
    """Lightweight stdlib-only comparison (difflib + re) - no paid semantic
    similarity service. Numeric-token disagreement (a digit run present in
    one transcript but not the other, e.g. "20" vs "200") is checked FIRST
    and independently forces materially_different=True regardless of edit
    distance, because a wrong quantity is far more dangerous than a spelling
    variant like "kbir" vs "kbeer".
    """
    a, b = a or "", b or ""
    nums_a, nums_b = set(_NUMBER_RE.findall(a)), set(_NUMBER_RE.findall(b))
    numeric_mismatch = nums_a != nums_b

    edit_ratio = difflib.SequenceMatcher(None, a, b).ratio()

    tokens_a, tokens_b = set(a.split()), set(b.split())
    if tokens_a or tokens_b:
        token_overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    else:
        token_overlap = 1.0

    if numeric_mismatch:
        return ComparisonResult(
            materially_different=True, token_overlap=token_overlap,
            edit_ratio=edit_ratio, numeric_mismatch=True,
            reason=f"numeric disagreement: {sorted(nums_a)} vs {sorted(nums_b)}")

    if (edit_ratio < settings.transcript_disagreement_edit_threshold or
            token_overlap < settings.transcript_disagreement_token_overlap_min):
        return ComparisonResult(
            materially_different=True, token_overlap=token_overlap,
            edit_ratio=edit_ratio, numeric_mismatch=False,
            reason=f"low similarity: edit_ratio={edit_ratio:.2f}, "
                  f"token_overlap={token_overlap:.2f}")

    return ComparisonResult(
        materially_different=False, token_overlap=token_overlap,
        edit_ratio=edit_ratio, numeric_mismatch=False, reason="")
