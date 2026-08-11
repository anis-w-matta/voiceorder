from app.config import settings
from app.schemas.enums import Intent

LINE_INTENTS = {Intent.add_order, Intent.repeat_order,
                Intent.repeat_order_adjusted, Intent.update_order}


class Flagger:
    def compute(self, *, transcript, extraction, lines, customer, ambiguity):
        f: list[str] = []
        if transcript.confidence < settings.transcript_conf_min:
            f.append("low_transcript_confidence")
        if transcript.duration < 1.5:
            f.append("very_short_audio")
        if transcript.duration > 180:
            f.append("long_audio")
        if transcript.duration > settings.max_audio_seconds:
            # max_audio_seconds was configured but never consulted anywhere,
            # so over-long recordings passed through unremarked.
            f.append("audio_too_long")
        if transcript.has_arabic:
            f.append("arabic_speech")
        if transcript.is_mixed:
            f.append("mixed_language")
        if any(l not in settings.expected_languages
               for l in transcript.languages):
            f.append("unexpected_language")
        # No unknown_caller flag here: compute() only runs for a known
        # customer. A call from an unrecognised number never reaches this
        # path - it becomes a Lead instead (see IntakePipeline.process).
        if any(l.item_nb is None for l in lines):
            f.append("unresolved_items")
        if any(l.qty is None for l in lines):
            f.append("missing_qty")
        if ambiguity:
            f.append(ambiguity)
        if len(extraction.intents) > 1:
            f.append("multi_intent")
        if Intent.cancel_order in extraction.intents:
            f.append("cancellation")
        if not lines and set(extraction.intents) & LINE_INTENTS:
            f.append("no_lines_extracted")
        return f
