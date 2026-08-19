"""Single source of truth for the audio file extensions this app accepts
and the MIME type each maps to for the Gemini API.

Shared by app/api/ingest.py (upload validation - constrains what the stored
extension can be, for filesystem safety) and
app/services/gemini_transcriber.py (Gemini file upload, which needs a MIME
type per extension). Kept in its own lightweight module - not in either of
those - so importing it doesn't pull FastAPI/router machinery into the
worker process or the google-genai SDK into the API process, and so the two
extension lists can't silently diverge the way they previously did as two
separately hand-maintained literals.
"""

AUDIO_MIME_BY_EXT: dict[str, str] = {
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
    ".aac": "audio/aac",
    ".m4a": "audio/aac",
    ".mp4": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".amr": "audio/amr",
    ".3gp": "audio/3gpp",
    ".webm": "audio/webm",
}
