import threading
import time

from app.services.rate_limiter import RateLimiter


def test_rate_limiter_enforces_minimum_interval():
    # rpm=1200 -> 0.05s minimum interval, fast enough to keep the test quick.
    limiter = RateLimiter(rpm=1200, max_concurrent=1)
    with limiter:
        pass
    start = time.monotonic()
    with limiter:
        pass
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05 - 0.01  # small tolerance for scheduler jitter


def test_rate_limiter_allows_immediate_first_call():
    limiter = RateLimiter(rpm=1, max_concurrent=1)
    start = time.monotonic()
    with limiter:
        pass
    assert time.monotonic() - start < 0.1


def test_rate_limiter_max_concurrent_blocks_second_acquirer():
    limiter = RateLimiter(rpm=100000, max_concurrent=1)
    holding = threading.Event()
    release = threading.Event()

    def hold():
        with limiter:
            holding.set()
            release.wait(timeout=2)

    t = threading.Thread(target=hold)
    t.start()
    assert holding.wait(timeout=1), "background thread never acquired"

    acquired_second = threading.Event()

    def try_acquire():
        with limiter:
            acquired_second.set()

    t2 = threading.Thread(target=try_acquire)
    t2.start()
    # Second acquirer must not succeed while the first still holds it.
    assert not acquired_second.wait(timeout=0.2)

    release.set()
    t.join(timeout=2)
    assert acquired_second.wait(timeout=2)
    t2.join(timeout=2)


def test_gemini_call_sites_share_one_limiter_instance():
    # Catches a future refactor that accidentally instantiates a second
    # limiter, which would silently double the effective RPM budget.
    import app.services.gemini_transcriber as trans_mod
    from app.services.rate_limiter import gemini_rate_limiter

    assert trans_mod.gemini_rate_limiter is gemini_rate_limiter
