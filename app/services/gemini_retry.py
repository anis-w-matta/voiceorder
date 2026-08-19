import httpx
import tenacity
from google.genai import errors as genai_errors

from app.config import settings


def _is_retryable(exc: BaseException) -> bool:
    """True for errors worth retrying: 429 (rate limit), any 5xx server
    error, and transient network failures. Never retries a 4xx client error
    other than 429 (bad request, auth failure - retrying identical input
    against the same credentials cannot fix those), and never retries a
    pydantic ValidationError from a malformed response body - a permanent
    failure a retry cannot fix either.
    """
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return exc.code == 429
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                        httpx.ReadError, TimeoutError, ConnectionError)):
        return True
    return False


def gemini_retry(max_attempts: int | None = None):
    """Decorator applying exponential backoff with jitter, capped, to a
    Gemini API call site - only for the retryable error classes above.
    reraise=True so a final failure surfaces as the original exception type
    (ServerError, ClientError, ...) rather than tenacity's own wrapper,
    which matters for callers that pattern-match on exception type.
    """
    return tenacity.retry(
        reraise=True,
        stop=tenacity.stop_after_attempt(
            max_attempts if max_attempts is not None else settings.gemini_max_retries),
        wait=tenacity.wait_exponential_jitter(
            initial=settings.gemini_retry_base_seconds,
            max=settings.gemini_retry_max_seconds),
        retry=tenacity.retry_if_exception(_is_retryable),
    )
