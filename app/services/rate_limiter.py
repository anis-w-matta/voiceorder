import threading
import time

from app.config import settings


class RateLimiter:
    """In-process sliding-interval limiter shared across every Gemini call
    site (transcriber + classifier), so the worker never exceeds the
    configured RPM/concurrency regardless of which service is calling.

    Deliberately in-process rather than Redis/DB-backed: the worker
    (app/worker.py) runs pipeline.process() sequentially in a single
    process with no threading of its own, so a plain semaphore + monotonic
    timestamp already solves the problem for free, without introducing new
    infrastructure. Revisit only if the worker ever becomes multi-process.
    """

    def __init__(self, rpm: int, max_concurrent: int):
        self._interval = 60.0 / max(rpm, 1)
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(max(max_concurrent, 1))
        self._last_call = 0.0

    def __enter__(self):
        self._sem.acquire()
        with self._lock:
            # Reserve this caller's slot atomically (spaced _interval apart
            # from the previously reserved slot) before releasing the lock,
            # so concurrent callers each get their own slot instead of all
            # waking up from the same wait computation at once.
            now = time.monotonic()
            next_call = max(now, self._last_call + self._interval)
            self._last_call = next_call
        wait = next_call - time.monotonic()
        if wait > 0:
            # Sleep outside the lock: otherwise every waiting thread
            # serializes on wall-clock sleep time and the configured
            # max_concurrent semaphore stops doing anything useful.
            time.sleep(wait)
        return self

    def __exit__(self, *exc):
        self._sem.release()
        return False


# Shared module-level instance - both GeminiTranscriber and GeminiClassifier
# import and use this same object, so their calls share one RPM budget
# rather than each getting their own (which would silently double the
# effective rate).
gemini_rate_limiter = RateLimiter(settings.gemini_rpm_limit,
                                  settings.gemini_max_concurrent)
