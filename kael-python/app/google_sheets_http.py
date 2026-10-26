"""Quota-aware HTTP client factory for gspread.

Requests are serialized and spaced to stay below Google's per-user Sheets API
quota with headroom. Rate-limit responses use bounded exponential backoff.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

try:
    from requests.exceptions import ConnectionError as RequestConnectionError
    from requests.exceptions import Timeout as RequestTimeout
except ImportError:  # pragma: no cover - requests is a project dependency
    RequestConnectionError = type("RequestConnectionError", (Exception,), {})
    RequestTimeout = type("RequestTimeout", (Exception,), {})

log = logging.getLogger("kael.google_sheets")
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_FINISHED = 0.0


def _get_status_code(exc: Exception) -> int | None:
    """Extract an HTTP status across gspread/google-auth exception versions."""
    candidates = [
        getattr(exc, "code", None),
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ]
    for value in candidates:
        try:
            status = int(value)
            if 100 <= status <= 599:
                return status
        except (TypeError, ValueError):
            continue
    return None


def _is_quota_error(exc: Exception, status: int | None) -> bool:
    if status == 429:
        return True
    message = str(exc).lower().replace("_", "")
    return status == 403 and any(token in message for token in (
        "ratelimitexceeded", "userratelimitexceeded", "quotaexceeded",
        "resourceexhausted", "too many requests", "quota exceeded",
    ))


def _should_retry(exc: Exception, method: str) -> bool:
    status = _get_status_code(exc)
    if _is_quota_error(exc, status):
        # A quota rejection means the operation was refused; retrying it is safe.
        return True
    # For ambiguous network/server failures, retry reads only. A write may have
    # reached Google before a timeout, and repeating it could duplicate an action.
    return method in {"get", "head"} and (
        status in {408, 500, 502, 503, 504}
        or isinstance(exc, (RequestTimeout, RequestConnectionError))
    )


def build_quota_aware_http_client(gspread_module: Any):
    """Build a gspread HTTP client class without importing gspread at module load.

    Defaults: one request every 1.25 seconds (about 48/minute), up to six
    retries for quota rejections using exponential backoff, capped at 32 seconds.
    Values may be tuned through KAEL_SHEETS_MIN_INTERVAL_SECONDS and
    KAEL_SHEETS_MAX_RETRIES.
    """
    base_http_client = gspread_module.HTTPClient
    interval = max(0.0, float(os.getenv("KAEL_SHEETS_MIN_INTERVAL_SECONDS", "1.25")))
    max_retries = max(0, min(8, int(os.getenv("KAEL_SHEETS_MAX_RETRIES", "6"))))

    class QuotaAwareHTTPClient(base_http_client):
        def request(self, *args, **kwargs):
            global _LAST_REQUEST_FINISHED
            method = str(args[0] if args else kwargs.get("method", "get")).lower()
            with _REQUEST_LOCK:
                for attempt in range(max_retries + 1):
                    remaining = interval - (time.monotonic() - _LAST_REQUEST_FINISHED)
                    if remaining > 0:
                        time.sleep(remaining)
                    try:
                        response = super().request(*args, **kwargs)
                    except Exception as exc:
                        _LAST_REQUEST_FINISHED = time.monotonic()
                        retryable = _should_retry(exc, method)
                        if not retryable or attempt >= max_retries:
                            raise
                        delay = min(2 ** attempt, 32) + random.uniform(0.0, 1.0)
                        log.warning(
                            "Google Sheets/Drive limit or transient read failure; "
                            "retry %s/%s in %.1fs (HTTP %s).",
                            attempt + 1, max_retries, delay, _get_status_code(exc),
                        )
                        time.sleep(delay)
                        continue
                    _LAST_REQUEST_FINISHED = time.monotonic()
                    return response
            raise RuntimeError("O limite de tentativas da API Google foi excedido.")

    QuotaAwareHTTPClient.__name__ = "QuotaAwareHTTPClient"
    return QuotaAwareHTTPClient
