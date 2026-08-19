from google.genai import errors as genai_errors

from app.services.gemini_retry import _is_retryable, gemini_retry


def test_is_retryable_server_error():
    assert _is_retryable(genai_errors.ServerError(500, {})) is True


def test_is_retryable_rate_limit_client_error():
    assert _is_retryable(genai_errors.ClientError(429, {})) is True


def test_is_not_retryable_auth_client_error():
    assert _is_retryable(genai_errors.ClientError(401, {})) is False


def test_is_not_retryable_bad_request_client_error():
    assert _is_retryable(genai_errors.ClientError(400, {})) is False


def test_is_not_retryable_generic_value_error():
    assert _is_retryable(ValueError("malformed response")) is False


def test_gemini_retry_retries_server_error_then_succeeds(monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "gemini_retry_base_seconds", 0.001)
    monkeypatch.setattr(config.settings, "gemini_retry_max_seconds", 0.01)

    calls = {"n": 0}

    @gemini_retry(max_attempts=3)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise genai_errors.ServerError(500, {})
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_gemini_retry_does_not_retry_auth_error(monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "gemini_retry_base_seconds", 0.001)
    monkeypatch.setattr(config.settings, "gemini_retry_max_seconds", 0.01)

    calls = {"n": 0}

    @gemini_retry(max_attempts=3)
    def always_unauthorized():
        calls["n"] += 1
        raise genai_errors.ClientError(401, {})

    try:
        always_unauthorized()
        assert False, "expected ClientError to propagate"
    except genai_errors.ClientError:
        pass
    assert calls["n"] == 1


def test_gemini_retry_gives_up_after_max_attempts(monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "gemini_retry_base_seconds", 0.001)
    monkeypatch.setattr(config.settings, "gemini_retry_max_seconds", 0.01)

    calls = {"n": 0}

    @gemini_retry(max_attempts=3)
    def always_fails():
        calls["n"] += 1
        raise genai_errors.ServerError(500, {})

    try:
        always_fails()
        assert False, "expected ServerError to propagate after retries exhausted"
    except genai_errors.ServerError:
        pass
    assert calls["n"] == 3
