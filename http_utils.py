#!/usr/bin/env python3
"""HTTP helpers for polite geo.admin.ch access."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
from typing import Optional

import requests


GEOADMIN_FAIR_USE_REQUESTS_PER_MINUTE = 40
MIN_REQUEST_INTERVAL_SECONDS = 60.0 / GEOADMIN_FAIR_USE_REQUESTS_PER_MINUTE
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_USER_AGENT = "Geo3Dprint/1.0 (https://github.com/aareblau/Geo3Dprint)"

_request_lock = threading.Lock()
_next_request_at = 0.0


def _wait_for_rate_slot() -> None:
    global _next_request_at
    with _request_lock:
        now = time.monotonic()
        wait_seconds = max(0.0, _next_request_at - now)
        _next_request_at = max(now, _next_request_at) + MIN_REQUEST_INTERVAL_SECONDS
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def request(
    method: str,
    url: str,
    *,
    session: Optional[requests.Session] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs,
) -> requests.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    requester = session.request if session is not None else requests.request
    last_response = None

    for attempt in range(max_retries + 1):
        _wait_for_rate_slot()
        try:
            response = requester(method, url, headers=headers, **kwargs)
        except requests.RequestException:
            if attempt >= max_retries:
                raise
            backoff = DEFAULT_BACKOFF_SECONDS * (2**attempt)
            time.sleep(backoff)
            continue
        last_response = response
        if response.status_code not in RETRY_STATUS_CODES or attempt >= max_retries:
            return response
        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        response.close()
        backoff = DEFAULT_BACKOFF_SECONDS * (2**attempt)
        time.sleep(max(retry_after or 0.0, backoff))

    # Unreachable, but keeps type checkers and readers honest.
    assert last_response is not None
    return last_response
