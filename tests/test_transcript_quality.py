from app.services.transcript_quality import assess_quality, compare_transcripts

# All synthetic - no audio files needed or available. The task's named
# audio files (Vo.m4a, etc.) are not present in this repository; these
# tests exercise the deterministic quality/comparison logic directly
# against transcript strings.


def test_empty_transcript_is_bad():
    assert assess_quality("", duration=5.0, gemini_confidence=0.9) == "bad"
    assert assess_quality("   ", duration=5.0, gemini_confidence=0.9) == "bad"


def test_garbage_repeated_char_is_bad():
    assert assess_quality("aaaaaaaaaaaaaa", duration=5.0,
                          gemini_confidence=0.9) == "bad"


def test_high_punctuation_ratio_is_bad():
    assert assess_quality("!!!???...,,,***&&&", duration=5.0,
                          gemini_confidence=0.9) == "bad"


def test_very_short_transcript_for_long_audio_is_questionable():
    assert assess_quality("hi", duration=30.0, gemini_confidence=0.9) == "questionable"


def test_short_transcript_for_short_audio_is_not_penalized_for_length():
    # duration <= 3s is not checked for chars-per-second, since a short
    # clip legitimately having little to say is not a length anomaly.
    result = assess_quality("ok thanks bye", duration=2.0, gemini_confidence=0.9)
    assert result == "good"


def test_repetition_is_questionable():
    assert assess_quality("the the the the item please", duration=5.0,
                          gemini_confidence=0.9) == "questionable"


def test_low_gemini_confidence_alone_is_questionable():
    result = assess_quality("baddi shwayet paint min fadlak", duration=5.0,
                            gemini_confidence=0.2)
    assert result == "questionable"


def test_clear_transcript_is_good():
    result = assess_quality("baddi 3 blue paint w 2 brush please", duration=5.0,
                            gemini_confidence=0.95)
    assert result == "good"


# ---- transcript comparison: numbers weigh more than spelling variants ----

def test_numeric_disagreement_outweighs_minor_spelling():
    cmp = compare_transcripts("baddi 20 kbir", "baddi 200 kbeer")
    assert cmp.materially_different is True
    assert cmp.numeric_mismatch is True
    assert "numeric" in cmp.reason


def test_minor_spelling_variant_alone_is_not_material():
    cmp = compare_transcripts("baddi kbir paint", "baddi kbeer paint")
    assert cmp.materially_different is False
    assert cmp.numeric_mismatch is False


def test_matching_numbers_with_wording_differences_not_material():
    cmp = compare_transcripts("baddi 5 paint cans", "baddi 5 paint cans please")
    assert cmp.numeric_mismatch is False


def test_completely_different_transcripts_are_material():
    cmp = compare_transcripts("baddi blue paint", "shu fi 3andkon discount")
    assert cmp.materially_different is True


def test_identical_transcripts_are_not_material():
    cmp = compare_transcripts("baddi 3 paint", "baddi 3 paint")
    assert cmp.materially_different is False
    assert cmp.numeric_mismatch is False


# ---- _build_transcript: single good attempt never triggers a 2nd call ----

def test_build_transcript_single_good_attempt_is_used_as_is():
    from app.services.gemini_transcriber import _build_transcript
    attempts = [{"text": "baddi 3 paint", "confidence": 0.95,
                "languages": ["en"], "quality": "good", "temperature": 0}]
    tr = _build_transcript(attempts, duration=4.0)
    assert tr.text == "baddi 3 paint"
    assert tr.quality == "good"
    assert tr.disagreement is False
    assert tr.attempts == attempts


def test_build_transcript_two_attempts_agreeing_no_disagreement():
    from app.services.gemini_transcriber import _build_transcript
    attempts = [
        {"text": "baddi 3 paint", "confidence": 0.4, "languages": ["en"],
         "quality": "questionable", "temperature": 0},
        {"text": "baddi 3 paint please", "confidence": 0.9, "languages": ["en"],
         "quality": "good", "temperature": 0.2},
    ]
    tr = _build_transcript(attempts, duration=4.0)
    assert tr.disagreement is False
    assert tr.text == "baddi 3 paint please"  # the good-quality attempt


def test_build_transcript_two_attempts_disagreeing_keeps_best_not_discarded():
    from app.services.gemini_transcriber import _build_transcript
    attempts = [
        {"text": "baddi 20 kbir", "confidence": 0.5, "languages": ["ar"],
         "quality": "questionable", "temperature": 0},
        {"text": "baddi 200 kbeer", "confidence": 0.4, "languages": ["ar"],
         "quality": "questionable", "temperature": 0.2},
    ]
    tr = _build_transcript(attempts, duration=4.0)
    assert tr.disagreement is True
    # Neither attempt is discarded - both are preserved in the audit trail.
    assert tr.attempts == attempts
    # Best available: no "good"-quality attempt exists, so highest
    # confidence wins (the first attempt, 0.5 > 0.4).
    assert tr.text == "baddi 20 kbir"
