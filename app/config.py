from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3"
    ollama_keep_alive: str = "30m"
    ollama_timeout: float = 120.0

    whisper_model: str = "large-v3"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"
    whisper_beam_size: int = 1

    audio_dir: str = "C:/voiceorder/audio"
    default_phone_region: str = "LB"
    expected_languages: list[str] = ["en", "ar"]

    fuzzy_accept: float = 0.85
    fuzzy_suggest: float = 0.60
    transcript_conf_min: float = -0.6      # avg_logprob: NEGATIVE scale
    max_audio_seconds: int = 300

    # Access control. Both default to off so an existing local deployment
    # keeps working, but on anything reachable beyond localhost they need to
    # be set: without api_key every endpoint (including the voice recordings
    # at /audio/{id}) is open, and without a known operator list the
    # X-Operator header is whatever the caller types.
    api_key: str | None = None
    operators: list[str] = []

    worker_poll_seconds: float = 2.0
    worker_max_attempts: int = 3
    worker_stale_minutes: int = 20

    # Bill delivery. Left blank on purpose - fill smtp_password in the local
    # .env, never in chat/source control. Without it, bill requests still
    # render the bill but return it undelivered rather than failing closed.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Voice Order Intake <noreply@voiceorder.local>"
    bill_recipient_email: str = "anis.w.matta@gmail.com"

    # Automated "customer requested a bill" notification (get_bill intent,
    # validated cust_nb/order_nb pair) - separate from bill_recipient_email
    # above, which is where the manually-triggered POST /bills/request full
    # HTML bill is sent.
    bill_request_notify_email: str = "anis.matta@net.usek.edu.lb"


settings = Settings()
