from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"

    audio_dir: str = str(_REPO_ROOT / "audio")
    default_phone_region: str = "LB"
    expected_languages: list[str] = ["en", "ar"]

    fuzzy_accept: float = 0.85
    fuzzy_suggest: float = 0.60
    # Threshold for the fuzzy alias substring/window match in
    # ItemResolver.find_in_text (rapidfuzz.fuzz.ratio, 0-100 scale).
    fuzzy_alias_threshold: int = 80
    # If the runner-up candidate is within this much of the top score, the
    # two items are effectively tied and picking one over the other isn't a
    # match, it's a coin flip - see TIE_EPSILON usage in item_resolver.py.
    resolver_tie_epsilon: float = 0.02
    # Subtracted from a candidate's score when its inferred size/color
    # conflicts with an explicitly-stated attribute. Chosen so a conflicting
    # candidate scoring at fuzzy_accept (0.85) drops to 0.50 - below
    # fuzzy_suggest (0.60), i.e. excluded from suggestions entirely.
    attribute_conflict_penalty: float = 0.35

    # Retry/backoff for Gemini API calls - see app/services/gemini_retry.py.
    # Only retryable errors (429, 5xx, transient network failures) consume
    # these attempts; permanent failures (auth, malformed request) never
    # retry regardless of this count.
    gemini_max_retries: int = 4
    gemini_retry_base_seconds: float = 1.0
    gemini_retry_max_seconds: float = 8.0

    # Conditional transcription retry - see app/services/transcript_quality.py
    # and GeminiTranscriber.transcribe. A second transcription attempt only
    # happens when the first is "questionable", capped by this maximum -
    # never retried unconditionally (Gemini bills per audio second).
    max_transcription_attempts: int = 2
    transcript_min_chars_per_second: float = 0.6
    transcript_repetition_threshold: float = 0.5
    transcript_disagreement_edit_threshold: float = 0.55
    transcript_disagreement_token_overlap_min: float = 0.5

    # Worker-level rate limiting for Gemini API calls, shared across the
    # transcriber and the command extractor (gemini_command_extractor.py) -
    # see app/services/rate_limiter.py. Every voice message now makes two
    # Gemini calls (transcription preview + command extraction) instead of
    # one, so this was raised from 10 - re-check against the account's
    # actual current limit in AI Studio and adjust if it's genuinely lower
    # or higher.
    gemini_rpm_limit: int = 20
    gemini_max_concurrent: int = 1
    # 0.0-1.0 scale - GeminiTranscriber reports its own self-rated
    # confidence per transcript (see PROMPT in gemini_transcriber.py); this
    # is the cutoff below which low_transcript_confidence fires. 0.5
    # matches the prompt's own definition of "large parts were a guess".
    transcript_conf_min: float = 0.5
    # Recordings longer than this skip transcription/classification entirely
    # (Gemini billing is per audio second, so there's no point paying to
    # transcribe something this long) and land straight in the review queue
    # flagged audio_too_long - see IntakePipeline.process.
    max_audio_seconds: int = 120

    # Access control. api_key defaults to off so an existing local deployment
    # keeps working, but on anything reachable beyond localhost it needs to
    # be set: without it every endpoint (including the voice recordings at
    # /audio/{id}) is open.
    api_key: str | None = None

    # catalog-service (item/customer/order_header/order_details/
    # qra_header/qra_detail) - see app/services/catalog_client.py.
    # catalog_api_key is sent as X-API-Key, matching that service's own
    # require_api_key gate (its own setting, off by default like this
    # one).
    catalog_service_url: str = "http://127.0.0.1:8100"
    catalog_api_key: str | None = None
    catalog_timeout_seconds: float = 10.0
    # How stale a "committing" PendingRequest must be before the worker's
    # reconciliation sweep re-drives its commit - see app/worker.py's
    # reconcile_stuck_commits(). Short: a stuck commit blocks that request
    # from being reviewed again until it resolves.
    commit_reconcile_stale_seconds: int = 30

    # Salesman auth (app/services/auth.py, app/api/auth.py). jwt_secret has
    # no default on purpose - every deployment must set its own via .env
    # (generate with `python -c "import secrets;print(secrets.token_hex(32))"`)
    # rather than sharing a checked-in value that would let anyone forge a
    # login token.
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 20160  # 14 days - mobile sessions stay signed in

    product_catalog_path: str = "Product.xlsm"
    # rapidfuzz score (0-100) a customer name/number must clear to be
    # returned as matched (below this: not_found) - see match_customer.py.
    customer_match_threshold: float = 75.0
    # If the runner-up customer candidate is within this of the top score,
    # the match is ambiguous rather than accepted - same tie-safety idea as
    # item_resolver.TIE_EPSILON, just scaled to the 0-100 rapidfuzz range.
    customer_match_tie_margin: float = 5.0
    # rapidfuzz score (0-100) an item candidate must clear before being
    # considered a plausible match at all - see match_item.py.
    item_fuzzy_threshold: float = 75.0
    # Score gap (0-100) below which the top-2 item candidates are treated
    # as tied/ambiguous rather than a confident top-1 pick.
    item_ambiguity_margin: float = 5.0
    # How many rapidfuzz candidates match_item.py generates before applying
    # numeric-conflict checks / confidence scoring.
    top_k_candidates: int = 10
    # Subtracted from an item candidate's score when its numeric pack/size
    # tokens (e.g. "12X4") conflict with tokens actually spoken (e.g.
    # "20X4") - large enough that a conflicting candidate can never
    # outscore a clean one at a merely-similar text score.
    numeric_conflict_penalty: float = 40.0

    worker_poll_seconds: float = 2.0
    worker_max_attempts: int = 3
    worker_stale_minutes: int = 20


settings = Settings()
