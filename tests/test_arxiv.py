from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
import respx

from fetcher.http import HttpClient, RateLimiter
from fetcher.sources.arxiv import API_URL, ArxivSource, build_query, category_term, parse_feed

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "arxiv_page.xml").read_bytes()
UTC = dt.UTC


def test_category_terms() -> None:
    assert category_term("cs.CV") == "cat:cs.CV"
    assert category_term("astro-ph.*") == "cat:astro-ph*"


def test_build_query() -> None:
    q = build_query(
        ["cs.CV", "astro-ph.*"],
        dt.datetime(2026, 10, 27, tzinfo=UTC),
        dt.datetime(2026, 11, 3, tzinfo=UTC),
    )
    assert q == "(cat:cs.CV OR cat:astro-ph*) AND submittedDate:[202610270000 TO 202611030000]"
    with pytest.raises(ValueError):
        build_query([], dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 2))


def test_parse_feed() -> None:
    entries, total, errors = parse_feed(PAGE)
    assert total == 4
    assert [e.arxiv_id for e in entries] == ["2610.01234", "2610.05678", "2610.09999"]
    assert len(errors) == 1 and "2610.04444" in errors[0]

    first = entries[0]
    assert first.version == 2
    assert first.title == "Sparse Diffusion for Real-Time Image Restoration"
    assert first.abstract.startswith("We present a diffusion model for image restoration")
    assert first.authors[0].affiliation == "Example University"
    assert first.authors[1].affiliation is None
    assert first.primary_category == "cs.CV"
    assert first.categories == ("cs.CV", "eess.IV")
    assert first.submitted == dt.datetime(2026, 10, 29, 17, 59, 59, tzinfo=UTC)
    assert first.comment is not None and first.comment.startswith("Accepted at CVPR")

    second = entries[1]
    assert second.journal_ref == "J. Graph Theory 112 (2026) 1-20"
    assert second.doi == "10.1002/jgt.99999"


def test_query_error_raises() -> None:
    with pytest.raises(ValueError, match="incorrect id format"):
        parse_feed((FIXTURES / "arxiv_error.xml").read_bytes())


@respx.mock
def test_recent_pages_until_total() -> None:
    page_template = PAGE.decode()
    # Two pages of size 2: first returns the fixture, second an empty feed.
    empty = page_template.split("<entry>")[0] + "</feed>"
    route = respx.get(API_URL).mock(
        side_effect=[httpx.Response(200, text=page_template), httpx.Response(200, text=empty)]
    )
    with HttpClient(sleep=lambda _: None) as http:
        source = ArxivSource(http, limiter=RateLimiter(0), page_size=2)
        result = source.recent(
            ["cs.CV"],
            since=dt.datetime(2026, 10, 27, tzinfo=UTC),
            until=dt.datetime(2026, 11, 3, tzinfo=UTC),
        )
    assert route.call_count == 2
    params = route.calls[0].request.url.params
    assert params["sortBy"] == "submittedDate"
    assert params["start"] == "0"
    assert route.calls[1].request.url.params["start"] == "2"
    assert len(result.entries) == 3
    assert any("got 3 of 4" in e for e in result.errors)
