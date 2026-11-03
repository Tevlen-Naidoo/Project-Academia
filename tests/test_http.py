from __future__ import annotations

import httpx
import pytest
import respx

from fetcher.http import USER_AGENT, HttpClient, RateLimiter, RetryableStatusError


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_calls() -> None:
    clock = FakeClock()
    limiter = RateLimiter(3.0, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.now += 1.0
    limiter.wait()
    limiter.wait()
    assert clock.sleeps == [pytest.approx(2.0), pytest.approx(3.0)]


@respx.mock
def test_retries_on_429_then_succeeds() -> None:
    route = respx.get("https://api.example/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(200, text="ok")]
    )
    slept: list[float] = []
    with HttpClient(sleep=slept.append) as http:
        response = http.get("https://api.example/x", limiter=RateLimiter(0))
    assert response.text == "ok"
    assert route.call_count == 3
    assert len(slept) == 2
    assert route.calls[0].request.headers["User-Agent"] == USER_AGENT


@respx.mock
def test_does_not_retry_client_errors() -> None:
    route = respx.get("https://api.example/x").mock(return_value=httpx.Response(404))
    with HttpClient(sleep=lambda _: None) as http, pytest.raises(httpx.HTTPStatusError):
        http.get("https://api.example/x", limiter=RateLimiter(0))
    assert route.call_count == 1


@respx.mock
def test_gives_up_after_max_attempts() -> None:
    route = respx.get("https://api.example/x").mock(return_value=httpx.Response(500))
    with HttpClient(attempts=3, sleep=lambda _: None) as http, pytest.raises(RetryableStatusError):
        http.get("https://api.example/x", limiter=RateLimiter(0))
    assert route.call_count == 3
