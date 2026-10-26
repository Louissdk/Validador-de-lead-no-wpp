from __future__ import annotations

from types import SimpleNamespace

import app.google_sheets_http as quota_http


class QuotaError(Exception):
    code = 429


class FakeHTTPClient:
    def __init__(self, auth=None, session=None):
        self.calls = 0

    def request(self, method, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise QuotaError("429 Too many requests")
        return {"ok": True, "method": method}


class FakeGspread:
    HTTPClient = FakeHTTPClient


def test_quota_aware_client_retries_429_with_backoff(monkeypatch):
    monkeypatch.setenv("KAEL_SHEETS_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("KAEL_SHEETS_MAX_RETRIES", "2")
    sleeps = []
    monkeypatch.setattr(quota_http.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(quota_http.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(quota_http, "_LAST_REQUEST_FINISHED", 0.0)

    client_class = quota_http.build_quota_aware_http_client(FakeGspread)
    client = client_class()
    result = client.request("get", "https://example.invalid")

    assert result == {"ok": True, "method": "get"}
    assert client.calls == 2
    assert sleeps == [1]


def test_ambiguous_timeout_is_retried_only_for_reads(monkeypatch):
    timeout = quota_http.RequestTimeout("read timed out")
    assert quota_http._should_retry(timeout, "get") is True
    assert quota_http._should_retry(timeout, "put") is False


def test_403_permission_error_is_not_treated_as_quota_error():
    exc = Exception("APIError: [403] The caller does not have permission")
    exc.code = 403
    assert quota_http._is_quota_error(exc, 403) is False


def test_403_rate_limit_error_is_treated_as_quota_error():
    exc = Exception("APIError: [403] userRateLimitExceeded")
    exc.code = 403
    assert quota_http._is_quota_error(exc, 403) is True
