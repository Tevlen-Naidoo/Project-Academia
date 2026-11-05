from __future__ import annotations

import pytest

from fetcher.http import HttpClient, RateLimiter
from fetcher.sources.arxiv import ArxivSource
from fetcher.sources.base import Source
from fetcher.sources.hackernews import HackerNewsSource
from fetcher.sources.huggingface import HuggingFaceSource
from fetcher.sources.openalex import OpenAlexSource
from fetcher.sources.semantic_scholar import SemanticScholarSource

SOURCES: list[type[Source]] = [
    ArxivSource,
    HackerNewsSource,
    HuggingFaceSource,
    OpenAlexSource,
    SemanticScholarSource,
]


@pytest.mark.parametrize("cls", SOURCES)
def test_every_source_sets_a_rate_limit(cls: type[Source]) -> None:
    source = cls(HttpClient())
    assert source._limiter.interval == cls.request_interval_s > 0


def test_api_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S2_API_KEY", "from-env")
    monkeypatch.setenv("OPENALEX_API_KEY", "")
    assert SemanticScholarSource(HttpClient())._api_key == "from-env"
    assert SemanticScholarSource(HttpClient(), api_key="given")._api_key == "given"
    assert SemanticScholarSource(HttpClient(), api_key="")._api_key is None  # "" turns it off
    assert OpenAlexSource(HttpClient())._api_key is None
    assert ArxivSource(HttpClient())._api_key is None  # no api_key_env


def test_a_given_limiter_wins() -> None:
    limiter = RateLimiter(0)
    assert HuggingFaceSource(HttpClient(), limiter=limiter)._limiter is limiter
