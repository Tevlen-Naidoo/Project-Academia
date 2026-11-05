"""What every paper source shares: the HTTP client, a polite rate limit, an
optional API key, lenient response models and a result that collects errors.

A new source subclasses `Source`, sets `request_interval_s` (and `api_key_env`
if the API takes a key), and fetches through `_get` / `_post`, which apply the
source's rate limit, retries and key. Each source keeps its own public method
(`recent`, `batch`, `by_dois`, ...) because the APIs answer different questions.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict

from fetcher.http import HttpClient, RateLimiter


class LenientModel(BaseModel):
    """A slice of an API response: unknown fields are ignored, so a source keeps
    working when the API adds fields. Fields may be filled by alias or by name."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


@dataclass(slots=True)
class SourceResult:
    """What a source returns. A bad item or a failed page is reported in `errors`
    rather than raised, so one problem never loses the rest of the run."""

    errors: list[str] = field(default_factory=list)


class Source:
    """Base class for a paper source."""

    request_interval_s: ClassVar[float]
    """Seconds to keep between calls, from the API's terms."""
    api_key_env: ClassVar[str | None] = None
    """The environment variable holding this API's key, if it takes one."""

    def __init__(
        self,
        http: HttpClient,
        *,
        limiter: RateLimiter | None = None,
        api_key: str | None = None,
    ) -> None:
        """`api_key=None` reads `api_key_env`; pass "" to run without a key."""
        self._http = http
        self._limiter = limiter or RateLimiter(self.request_interval_s)
        if api_key is None and self.api_key_env:
            api_key = os.environ.get(self.api_key_env)
        self._api_key = api_key or None

    def _get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return self._http.get(url, limiter=self._limiter, params=params, headers=headers)

    def _post(
        self,
        url: str,
        *,
        json: Any,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return self._http.post(
            url, limiter=self._limiter, json=json, params=params, headers=headers
        )
