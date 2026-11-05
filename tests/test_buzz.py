from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fetcher import enrich, pipeline
from fetcher.http import HttpClient, RateLimiter
from fetcher.models import Paper
from fetcher.sources.hackernews import SEARCH_URL, HackerNewsSource, paper_key
from fetcher.sources.huggingface import DAILY_URL, HuggingFaceSource
from fetcher.topics import load_topics

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 3)
NOW = dt.datetime(2026, 11, 3, 6, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://arxiv.org/abs/2610.01234v2", ("arxiv", "2610.01234")),
        ("http://arxiv.org/pdf/2610.01234.pdf", ("arxiv", "2610.01234")),
        ("https://arxiv.org/html/2610.01234v1", ("arxiv", "2610.01234")),
        ("https://arxiv.org/abs/hep-th/9901001", ("arxiv", "hep-th/9901001")),
        ("https://doi.org/10.1002/JGT.99999", ("doi", "10.1002/jgt.99999")),
        ("https://example.com/arxiv-explained", None),
    ],
)
def test_paper_key(url: str, key: tuple[str, str] | None) -> None:
    assert paper_key(url) == key


@respx.mock
def test_huggingface_days_and_max() -> None:
    rows = json.loads((FIXTURES / "hf_daily.json").read_text())
    later = [{"paper": {"id": "2610.01234", "upvotes": 50}, "numComments": 9}]
    route = respx.get(DAILY_URL).mock(
        side_effect=[
            httpx.Response(200, json=later),
            httpx.Response(200, json=rows),
            httpx.Response(500),
        ]
    )
    with HttpClient(attempts=1, sleep=lambda _: None) as http:
        result = HuggingFaceSource(http, limiter=RateLimiter(0)).recent(until=TODAY, days=3)
    dates = [c.request.url.params["date"] for c in route.calls]
    assert dates == ["2026-11-03", "2026-11-02", "2026-11-01"]
    assert result.buzz["2610.01234"].upvotes == 87  # highest of the two days
    assert result.buzz["2610.01234"].comments == 9
    assert result.buzz["2610.07777"].upvotes == 12
    assert len(result.errors) == 1 and "2026-11-01" in result.errors[0]


@respx.mock
def test_hackernews_sums_stories_per_paper() -> None:
    body = (FIXTURES / "hn_search.json").read_bytes()
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, content=body))
    with HttpClient(sleep=lambda _: None) as http:
        result = HackerNewsSource(http, limiter=RateLimiter(0)).recent(
            since=NOW - dt.timedelta(days=30)
        )
    # one call per query (arxiv.org, doi.org), one page each
    assert route.call_count == 2
    params = route.calls[0].request.url.params
    assert params["restrictSearchableAttributes"] == "url"
    assert (
        params["numericFilters"] == f"created_at_i>{int((NOW - dt.timedelta(days=30)).timestamp())}"
    )
    # both queries return the same fixture, so each story is counted twice
    assert result.by_arxiv["2610.01234"].points == 2 * (240 + 10)
    assert result.by_doi["10.1002/jgt.99999"].comments == 2 * 12
    assert result.stories == 2 * 3


@respx.mock
def test_hackernews_pages() -> None:
    page = {"nbPages": 2, "hits": [{"url": "https://arxiv.org/abs/2610.00001", "points": 1}]}
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=page))
    with HttpClient(sleep=lambda _: None) as http:
        HackerNewsSource(http, limiter=RateLimiter(0)).recent(since=NOW)
    assert [c.request.url.params["page"] for c in route.calls] == ["0", "1", "0", "1"]


def test_buzz_moves_presort(paper_data: dict[str, Any]) -> None:
    quiet = Paper.model_validate(paper_data)
    loud = Paper.model_validate(
        {**paper_data, "id": "arxiv:2410.99999", "ids": {"arxiv": "2410.99999"}}
    )
    loud.buzz.hf_upvotes = 40
    loud.buzz.hn_points = 100
    assert enrich.buzz_score(loud) == 90
    assert enrich.presort_key(loud) > enrich.presort_key(quiet)


@respx.mock
def test_pipeline_applies_buzz() -> None:
    from fetcher.sources.arxiv import API_URL, ArxivSource
    from fetcher.sources.openalex import OpenAlexSource
    from fetcher.sources.semantic_scholar import SemanticScholarSource

    respx.get(API_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "arxiv_page.xml").read_bytes())
    )
    respx.get(DAILY_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "hf_daily.json").read_bytes())
    )
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "hn_search.json").read_bytes())
    )
    with HttpClient(attempts=1, sleep=lambda _: None) as http:
        candidates = pipeline.run(
            http,
            load_topics(),
            pipeline.RunOptions(today=TODAY),
            now=NOW,
            arxiv=ArxivSource(http, limiter=RateLimiter(0)),
            semantic_scholar=SemanticScholarSource(http, api_key="", limiter=RateLimiter(0)),
            openalex=OpenAlexSource(http, api_key="", limiter=RateLimiter(0)),
            huggingface=HuggingFaceSource(http, limiter=RateLimiter(0)),
            hackernews=HackerNewsSource(http, limiter=RateLimiter(0)),
        )
    papers = {p.id: p for p in candidates.papers}
    restoration = papers["arxiv:2610.01234"]
    assert restoration.buzz.hf_upvotes == 87
    assert restoration.buzz.hn_points == 2 * (240 + 10)
    colouring = papers["arxiv:2610.05678"]
    assert colouring.buzz.hn_points == 2 * 55  # matched by its journal DOI
    assert colouring.buzz.hf_upvotes is None  # not featured: unknown, not zero
    assert candidates.sources["huggingface"].fetched == 2
    assert candidates.sources["hackernews"].fetched == 6
