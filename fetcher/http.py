"""Shared HTTP client: polite User-Agent, per-host rate limits, retries."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

USER_AGENT = "project-academia/0.1 (+https://github.com/Tevlen-Naidoo/project-academia)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class RateLimiter:
    """Keeps at least `interval` seconds between calls to `wait()`."""

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self.interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now


class RetryableStatusError(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code} from {response.request.url}")
        self.response = response


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableStatusError | httpx.TransportError)


class HttpClient:
    """A thin wrapper over httpx with one rate limiter per source."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        attempts: int = 5,
        backoff_max: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
        )
        self._attempts = attempts
        self._backoff_max = backoff_max
        self._sleep = sleep

    def get(
        self,
        url: str,
        *,
        limiter: RateLimiter,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        retrying = Retrying(
            stop=stop_after_attempt(self._attempts),
            wait=wait_exponential(multiplier=2, max=self._backoff_max),
            retry=retry_if_exception(_is_retryable),
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                limiter.wait()
                response = self._client.get(url, params=params, headers=headers)
                if response.status_code in RETRY_STATUSES:
                    raise RetryableStatusError(response)
                response.raise_for_status()
                return response
        raise AssertionError("unreachable")  # pragma: no cover

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
