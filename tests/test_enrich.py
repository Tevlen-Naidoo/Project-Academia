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
from fetcher.models import Paper, PublicationStatus, StatusEvidence, VenueType
from fetcher.sources.arxiv import API_URL as ARXIV_URL
from fetcher.sources.arxiv import ArxivSource
from fetcher.sources.hackernews import HackerNewsSource
from fetcher.sources.huggingface import HuggingFaceSource
from fetcher.sources.openalex import WORKS_URL, OAWork, OpenAlexSource, arxiv_doi
from fetcher.sources.semantic_scholar import BATCH_URL, S2Paper, SemanticScholarSource
from fetcher.topics import load_topics

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 3)
NOW = dt.datetime(2026, 11, 3, 6, 0, tzinfo=dt.UTC)


def _s2_rows() -> list[Any]:
    rows: list[Any] = json.loads((FIXTURES / "s2_batch.json").read_text())
    return rows


def _oa_work() -> OAWork:
    data = json.loads((FIXTURES / "openalex_works.json").read_text())
    return OAWork.model_validate(data["results"][0])


def _paper(paper_data: dict[str, Any], **changes: Any) -> Paper:
    return Paper.model_validate({**paper_data, **changes})


def test_s2_venue_upgrades_preprint(paper_data: dict[str, Any]) -> None:
    paper = _paper(paper_data)
    enrich.apply_s2(paper, S2Paper.model_validate(_s2_rows()[0]))
    assert paper.ids.s2 == "s2restoration"
    assert paper.ids.doi == "10.48550/arXiv.2410.12345"  # kept from fixture, not replaced
    assert paper.citations.total == 12
    assert paper.citations.influential == 2
    assert paper.tldr == "A fast diffusion model for image restoration."
    assert paper.publication.status is PublicationStatus.PUBLISHED
    assert paper.publication.evidence is StatusEvidence.S2_VENUE
    assert paper.publication.venue_type is VenueType.CONFERENCE
    assert paper.dates.published == dt.date(2026, 6, 15)


def test_s2_arxiv_venue_is_not_published(paper_data: dict[str, Any]) -> None:
    paper = _paper(paper_data)
    s2 = S2Paper.model_validate(
        {"paperId": "p", "venue": "arXiv.org", "publicationTypes": ["JournalArticle"],
         "journal": {"name": "ArXiv"}, "citationCount": 0}
    )  # fmt: skip
    enrich.apply_s2(paper, s2)
    assert paper.publication.status is PublicationStatus.PREPRINT
    assert paper.citations.total == 0


def test_s2_ignores_arxiv_doi(paper_data: dict[str, Any]) -> None:
    data = {**paper_data, "ids": {"arxiv": "2410.12345"}}
    paper = _paper(data)
    s2 = S2Paper.model_validate(
        {"paperId": "p", "externalIds": {"DOI": "10.48550/arXiv.2410.12345"}}
    )
    enrich.apply_s2(paper, s2)
    assert paper.ids.doi is None


def test_weaker_evidence_never_downgrades(paper_data: dict[str, Any]) -> None:
    paper = _paper(paper_data)
    enrich.apply_s2(paper, S2Paper.model_validate(_s2_rows()[0]))
    enrich.apply_openalex(paper, _oa_work(), load_topics())
    assert paper.publication.evidence is StatusEvidence.S2_VENUE
    assert paper.publication.venue == "Computer Vision and Pattern Recognition"


def test_openalex_published_version(paper_data: dict[str, Any]) -> None:
    paper = _paper(paper_data)
    enrich.apply_openalex(paper, _oa_work(), load_topics())
    assert paper.ids.openalex == "W4400000001"
    assert paper.citations.by_year == {2026: 3}
    assert paper.citations.total == 3
    assert paper.publication.evidence is StatusEvidence.OPENALEX_PUBLISHED_VERSION
    assert paper.publication.venue == "Journal of Graph Theory"
    assert paper.topics == ["graph-theory"]


def test_openalex_abstract_rebuild() -> None:
    assert _oa_work().abstract() == "Every graph is colourable."
    assert OAWork(id="W1").abstract() is None


def test_openalex_subfield_ids() -> None:
    assert _oa_work().subfield_ids == [2607]


def test_derive_signals(paper_data: dict[str, Any]) -> None:
    paper = _paper(paper_data)
    paper.citations.total = 30
    paper.citations.by_year = {2026: 20, 2025: 12}
    paper.dates.submitted = dt.date(2026, 5, 3)  # about 6 months before TODAY
    enrich.derive_signals(paper, TODAY)
    assert paper.citations.per_month == pytest.approx(30 / (184 / 30.44), rel=1e-3)
    # 20 this year + 12 * (fraction of 2025 still inside the last 12 months)
    assert paper.citations.last_12_months == 22


def test_derive_signals_young_paper(paper_data: dict[str, Any]) -> None:
    paper = _paper(paper_data)
    paper.citations.total = 4
    enrich.derive_signals(paper, TODAY)  # submitted 5 days ago
    assert paper.citations.per_month == 4.0  # floor of one month


def test_select_candidates_keeps_small_topics(paper_data: dict[str, Any]) -> None:
    def make(i: int, topic: str, cites: int) -> Paper:
        p = _paper(
            paper_data,
            id=f"arxiv:2410.{i:05d}",
            ids={"arxiv": f"2410.{i:05d}"},
            topics=[topic],
        )
        p.citations.influential = cites
        return p

    papers = [make(i, "ml", 100 + i) for i in range(10)] + [make(50, "astronomy", 0)]
    chosen = enrich.select_candidates(papers, per_topic=3, cap=100)
    assert [p.ids.arxiv for p in chosen] == ["2410.00009", "2410.00008", "2410.00007", "2410.00050"]
    assert len(enrich.select_candidates(papers, per_topic=3, cap=2)) == 2


@respx.mock
def test_s2_batch_sends_key_and_chunks() -> None:
    route = respx.post(BATCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=_s2_rows()),
            httpx.Response(200, json=[None]),
        ]
    )
    with HttpClient(sleep=lambda _: None) as http:
        source = SemanticScholarSource(http, api_key="k", limiter=RateLimiter(0), batch_size=2)
        result = source.batch(["ARXIV:2610.01234", "ARXIV:2610.05678", "ARXIV:2610.09999"])
    assert route.call_count == 2
    assert route.calls[0].request.headers["x-api-key"] == "k"
    assert json.loads(route.calls[0].request.content) == {
        "ids": ["ARXIV:2610.01234", "ARXIV:2610.05678"]
    }
    assert list(result.papers) == ["ARXIV:2610.01234"]
    assert result.errors == []


@respx.mock
def test_s2_bad_batch_is_reported() -> None:
    respx.post(BATCH_URL).mock(return_value=httpx.Response(200, json={"error": "x"}))
    with HttpClient(sleep=lambda _: None) as http:
        source = SemanticScholarSource(http, api_key="", limiter=RateLimiter(0))
        result = source.batch(["ARXIV:1"])
    assert result.papers == {}
    assert "unexpected response" in result.errors[0]


@respx.mock
def test_openalex_filter_and_key() -> None:
    route = respx.get(WORKS_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "openalex_works.json").read_bytes())
    )
    with HttpClient(sleep=lambda _: None) as http:
        source = OpenAlexSource(http, api_key="oa", limiter=RateLimiter(0))
        result = source.by_dois(["10.1002/JGT.99999", arxiv_doi("2610.05678")])
    params = route.calls[0].request.url.params
    assert params["filter"] == "doi:10.1002/jgt.99999|10.48550/arxiv.2610.05678"
    assert params["api_key"] == "oa"
    assert list(result.works) == ["10.1002/jgt.99999"]


@respx.mock
def test_full_pipeline_with_enrichment(tmp_path: Path) -> None:
    respx.get(ARXIV_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "arxiv_page.xml").read_bytes())
    )
    respx.post(BATCH_URL).mock(return_value=httpx.Response(200, json=_s2_rows()))
    respx.get(WORKS_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "openalex_works.json").read_bytes())
    )
    with HttpClient(sleep=lambda _: None) as http:
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
    path = pipeline.write(candidates, tmp_path)
    papers = {p.id: p for p in candidates.papers}

    restoration = papers["arxiv:2610.01234"]
    assert restoration.publication.evidence is StatusEvidence.S2_VENUE
    assert restoration.citations.total == 12
    # S2 dates the proceedings 2026-06-15, before this arXiv version: 12 cites over 141 days
    assert restoration.citations.per_month == pytest.approx(12 / (141 / 30.44), abs=1e-3)

    colouring = papers["arxiv:2610.05678"]
    assert colouring.publication.evidence is StatusEvidence.OPENALEX_PUBLISHED_VERSION
    assert colouring.ids.openalex == "W4400000001"

    assert set(candidates.sources) == {
        "arxiv",
        "openalex_waves",
        "semantic_scholar",
        "openalex",
        "huggingface",
        "hackernews",
    }
    assert candidates.sources["semantic_scholar"].fetched == 1
    assert candidates.sources["openalex"].fetched == 1
    assert path.exists()


@respx.mock
def test_s2_rejected_batch_is_split_and_retried() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["ids"]
        if len(ids) > 8:
            return httpx.Response(400, json={"error": "Response would exceed maximum size"})
        return httpx.Response(200, json=[{"paperId": i} for i in ids])

    route = respx.post(BATCH_URL).mock(side_effect=answer)
    ids = sorted(f"ARXIV:{i:02d}" for i in range(16))
    with HttpClient(sleep=lambda _: None) as http:
        source = SemanticScholarSource(http, api_key="", limiter=RateLimiter(0))
        result = source.batch(ids)
    assert sorted(result.papers) == ids
    assert result.errors == []
    assert route.call_count == 3  # 16 rejected, then 8 + 8


@respx.mock
def test_s2_split_stops_and_reports_small_bad_batch() -> None:
    respx.post(BATCH_URL).mock(return_value=httpx.Response(400, text="Invalid id"))
    with HttpClient(sleep=lambda _: None) as http:
        source = SemanticScholarSource(http, api_key="", limiter=RateLimiter(0))
        result = source.batch(["ARXIV:bad"] * 4)
    assert result.papers == {}
    assert result.errors == ["batch at 0 (4 ids): HTTP 400: Invalid id"]


@respx.mock
def test_s2_rate_limit_after_retries_is_reported_not_split() -> None:
    route = respx.post(BATCH_URL).mock(return_value=httpx.Response(429))
    with HttpClient(attempts=2, sleep=lambda _: None) as http:
        source = SemanticScholarSource(http, api_key="", limiter=RateLimiter(0))
        result = source.batch(["ARXIV:1"] * 20)
    assert route.call_count == 2
    assert len(result.errors) == 1 and "429" in result.errors[0]


@respx.mock
def test_s2_none_found_is_not_an_error_and_not_split() -> None:
    route = respx.post(BATCH_URL).mock(
        return_value=httpx.Response(400, json={"error": "No valid paper ids given"})
    )
    with HttpClient(sleep=lambda _: None) as http:
        source = SemanticScholarSource(http, api_key="", limiter=RateLimiter(0))
        result = source.batch([f"ARXIV:2611.{i:05d}" for i in range(100)])
    assert route.call_count == 1
    assert result.papers == {}
    assert result.errors == []


@respx.mock
def test_s2_time_budget_stops_lookups() -> None:
    now = [0.0]

    def answer(request: httpx.Request) -> httpx.Response:
        now[0] += 100  # each call "takes" 100 s
        ids = json.loads(request.content)["ids"]
        return httpx.Response(200, json=[None] * len(ids))

    route = respx.post(BATCH_URL).mock(side_effect=answer)
    with HttpClient(sleep=lambda _: None) as http:
        source = SemanticScholarSource(
            http,
            api_key="",
            limiter=RateLimiter(0),
            batch_size=10,
            time_budget_s=250,
            clock=lambda: now[0],
        )
        result = source.batch([f"ARXIV:{i}" for i in range(100)])
    assert route.call_count == 3
    assert result.errors == ["stopped after 250s; 70 ids not looked up"]
