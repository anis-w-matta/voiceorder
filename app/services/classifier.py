from ollama import Client
from tenacity import retry, stop_after_attempt, wait_fixed

from app.config import settings
from app.schemas.enums import Intent
from app.schemas.extraction import Extraction

SYSTEM_PROMPT = """You extract structured order requests from transcribed \
phone messages for a trading company in Lebanon.

The transcript comes from speech and MAY MIX ARABIC AND ENGLISH, sometimes
within one sentence. Arabic may appear in Arabic script or transliterated
into Latin letters (Arabizi, e.g. "baddi", "shu", "kifak", using digits for
letters with no Latin equivalent: 3=ع, 7=ح, 2=ء/ق). Handle all of these.

Return every intent that clearly applies. Most messages have exactly one;
it is rare for a real message to need more than two.

  add_order              wants to order products
  repeat_order           wants the same as their last order, unchanged
  repeat_order_adjusted  wants their last order with changes
  update_order           wants to change an existing order
  cancel_order           wants to cancel an existing order
  get_invoice            wants an invoice (fatoura)
  get_bill               wants a bill or statement of account
  catalogue_request      asking what products exist, are available, or in
                         stock - NOT the same as ordering. "do you have
                         blue paint" / "shu 3andkon min alwan" is
                         catalogue_request; only use add_order once the
                         customer actually asks to receive a quantity.
  other                  none of the above

For each product mentioned output one line:
  raw_text  the exact words for THAT product only, in the original script -
           not the whole message. If two products are named in one
           sentence, split them into two lines, each scoped to its own
           product mention.
  raw_lang  "en", "ar" or "mixed". This is about the language of the
           WORDS, not the script: Arabic spoken/written phonetically in
           Latin letters (Arabizi) is still "ar", never "mixed" just
           because it uses Latin characters. Use "mixed" only when a line
           itself combines real English words and Arabic words together
           (e.g. "baddi order 3 blue paint w 2 brush" mixes "order",
           "blue paint" (English) with "baddi", "w" (Arabic)).
  product   the product name or code, or null if unsure
  qty       the number, or null. Digits and Arabic numerals both count. The
           customer may also spell a quantity out as an Arabic/Arabizi
           number word instead of a digit - if, and only if, one of these
           exact words is actually present in the customer's own message,
           translate it to the matching number: wa7de/wehde=1, tinten/
           tnein=2, tlet(e)=3, arba3a=4, khamse=5, sitte=6, sabe3a=7,
           tmene=8, tese3a=9, 3ashra=10. Never introduce a quantity, or any
           of these words, that the customer did not say.
  uom       box, carton, piece, kg, litre, 3ilbe, kartouna... or null
  change    add, remove, increase or decrease - adjusted repeats only.
           If the customer excludes something ("without X", "بدون X",
           "bas mish X"), still output a line for X with change=remove -
           do not just drop it into missing.

RULES
- Never invent a product code. If unsure leave product null and keep the
  words in raw_text.
- Never translate raw_text. Copy it exactly as spoken.
- Do not correct or complete anything you are not confident about.
- Do not extract prices.
- Intent labels are always in English regardless of the spoken language.
- List anything the customer left out in missing."""

MAX_PLAUSIBLE_INTENTS = 3


def _ground_lines(text: str, extraction: Extraction) -> Extraction:
    """Drop any line whose raw_text isn't actually present in the input.

    RULES already tells the model raw_text must be copied verbatim, which
    makes this a cheap, prompt-independent way to catch fabrication rather
    than trusting the model to follow that rule. Observed on short/vague
    input (a bare "5", "same as before but add 2 more brushes"): the model
    invented entire multi-line orders, sometimes lifting the exact example
    words out of this file's own system prompt (e.g. "khamse", "tese3a")
    as if the customer had said them. A fabricated line's raw_text is
    never a substring of what was actually said, so this check is a hard
    backstop against that failure mode independent of prompt wording.
    """
    low = text.lower()
    kept = [ln for ln in extraction.lines
            if ln.raw_text and ln.raw_text.strip().lower() in low]
    if len(kept) == len(extraction.lines):
        return extraction
    return extraction.model_copy(update={"lines": kept})


class OllamaClassifier:
    def __init__(self, host=None, model=None, keep_alive=None, timeout=None):
        self.client = Client(host=host or settings.ollama_host,
                             timeout=timeout or settings.ollama_timeout)
        self.model = model or settings.ollama_model
        self.keep_alive = keep_alive or settings.ollama_keep_alive
        self.schema = self._output_schema()

    @staticmethod
    def _output_schema() -> dict:
        # Every top-level field on Extraction has a Python default, so
        # pydantic emits no "required" list. Ollama's grammar-constrained
        # decoder then treats omitting a field entirely (rather than
        # emitting an empty list/null for it) as the cheapest valid
        # completion, and small models take that shortcut - "lines" comes
        # back missing even when the transcript clearly names a product.
        # Forcing every field to be present forces the decoder to actually
        # attempt to fill it in; an empty [] / null is still legal wherever
        # that's the right answer.
        schema = Extraction.model_json_schema()
        schema["required"] = list(schema["properties"])
        return schema

    def warm(self) -> bool:
        try:
            self.client.chat(model=self.model,
                             messages=[{"role": "user", "content": "hi"}],
                             keep_alive=self.keep_alive)
            return True
        except Exception:
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    def _call(self, text: str, temperature: float) -> Extraction:
        resp = self.client.chat(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": text}],
            format=self.schema,
            options={"temperature": temperature},
            keep_alive=self.keep_alive,
        )
        return Extraction.model_validate_json(resp["message"]["content"])

    def classify(self, text: str) -> Extraction:
        # An empty/blank transcript has nothing for the model to extract
        # from, but a constrained-JSON decoder still has to emit *some*
        # legal completion - observed behaviour was inventing a plausible-
        # looking multi-line order out of nothing. Short-circuit before
        # that ever gets a chance to happen.
        if not text or not text.strip():
            return Extraction(intents=[Intent.other])

        # temperature 0 is deterministic, so a message that decodes to an
        # implausible number of intents (the model dumping most/all of the
        # enum instead of actually classifying - observed on plain,
        # unambiguous input) will decode to the exact same bad result every
        # time at temperature 0. Retrying only helps once temperature moves
        # off zero, so the fallback attempts nudge it up instead of
        # repeating the identical call. @retry on _call still separately
        # handles genuine transient failures (timeouts etc) at each step.
        for temperature in (0, 0.3, 0.6):
            extraction = self._call(text, temperature)
            if len(extraction.intents) <= MAX_PLAUSIBLE_INTENTS:
                return _ground_lines(text, extraction)

        # Every attempt, including two at nonzero temperature, still came
        # back with an implausible spread of intents - a genuine (if rare)
        # small-model failure on this input rather than a transient one.
        # Don't crash the pipeline over it: fall back to "other" and leave
        # a note so a human looks at it directly instead of the request
        # silently carrying every intent's side effects at once.
        return Extraction(
            intents=[Intent.other],
            missing=["automatic classification was unreliable for this "
                    "message - flagged for manual review"])
