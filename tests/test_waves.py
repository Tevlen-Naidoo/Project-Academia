from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import respx

from fetcher import merge
from fetcher.funnels.fresh import paper_from_entry
from fetcher.funnels.waves import paper_from_work, windows
from fetcher.http import HttpClient, RateLimiter
from fetcher.models import Funnel, Paper, PublicationStatus, StatusEvidence
from fetcher.sources.arxiv import parse_feed
from fetcher.sources.hackernews import HackerNewsSource
from fetcher.sources.huggingface import HuggingFaceSource
from fetcher.sources.openalex import WORKS_URL, OAWork, OpenAlexSource
from fetcher.topics import load_topics

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 3)
NOW = dt.datetime(2026, 11, 3, 6, 0, tzinfo=dt.UTC)


def _works() -> list[OAWork]:
    data = json.loads((FIXTURES / "openalex_waves.json").read_text())
    return [OAWork.model_validate(r) for r in data["results"]]


def test_windows_do_not_overlap() -> None:
    recent, older = windows(TODAY)
    assert recent.until == TODAY
    assert older.until < recent.since
    assert older.min_citations > recent.min_citations


def test_arxiv_id_from_doi_or_landing_page() -> None:
    vit, dark, _ = _works()
    assert vit.arxiv_id == "2505.11111"
    assert dark.arxiv_id is None
    via_page = OAWork.model_validate(
        {"id": "W9", "locations": [{"landing_page_url": "http://arxiv.org/pdf/2401.00001v2.pdf"}]}
    )
    assert via_page.arxiv_id == "2401.00001"


def test_paper_from_arxiv_preprint_work() -> None:
    vit = paper_from_work(_works()[0], topics=load_topics(), today=TODAY, now=NOW)
    assert vit is not None
    assert vit.id == "arxiv:2505.11111"
    assert vit.ids.doi is None  # arXiv's own DOI is not a publisher DOI
    assert vit.ids.openalex == "W4500000001"
    assert vit.title == "Scaling Laws for Vision Transformers"
    assert vit.abstract == "We fit scaling laws for image models."
    assert [a.name for a in vit.authors] == ["Grace Hopper"]
    assert vit.topics == ["vision", "image-processing"]  # "image" in the abstract
    assert vit.dates.submitted == dt.date(2026, 5, 20)
    assert vit.citations.total == 180
    assert vit.publication.status is PublicationStatus.PREPRINT
    assert vit.funnel == [Funnel.WAVES]
    assert str(vit.links.abstract_page) == "https://arxiv.org/abs/2505.11111"


def test_paper_from_journal_work() -> None:
    dark = paper_from_work(_works()[1], topics=load_topics(), today=TODAY, now=NOW)
    assert dark is not None
    assert dark.id == "doi:10.1038/s41586-026-00001-x"
    assert dark.topics == ["astronomy"]
    assert dark.dates.published == dt.date(2025, 9, 1)
    assert dark.publication.evidence is StatusEvidence.OPENALEX_PUBLISHED_VERSION
    assert dark.publication.venue == "Nature"
    assert str(dark.links.abstract_page) == "https://doi.org/10.1038/s41586-026-00001-x"


def test_work_without_abstract_is_dropped() -> None:
    assert paper_from_work(_works()[2], topics=load_topics(), today=TODAY, now=NOW) is None


@respx.mock
def test_most_cited_query() -> None:
    route = respx.get(WORKS_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "openalex_waves.json").read_bytes())
    )
    with HttpClient(sleep=lambda _: None) as http:
        source = OpenAlexSource(http, api_key="", limiter=RateLimiter(0))
        works = source.most_cited(1707, since=dt.date(2026, 5, 5), until=TODAY, min_citations=5)
    params = route.calls[0].request.url.params
    assert params["filter"] == (
        "primary_topic.subfield.id:1707,from_publication_date:2026-05-05,"
        "to_publication_date:2026-11-03,cited_by_count:>5,has_abstract:true,"
        "type:article|preprint"
    )
    assert params["sort"] == "cited_by_count:desc"
    assert params["per_page"] == "100"
    assert "api_key" not in params
    assert len(works) == 3


def _fresh_paper(paper_data: dict[str, Any], arxiv: str, title: str) -> Paper:
    return Paper.model_validate(
        {**paper_data, "id": f"arxiv:{arxiv}", "ids": {"arxiv": arxiv}, "title": title}
    )


def test_merge_by_arxiv_id(paper_data: dict[str, Any]) -> None:
    fresh = _fresh_paper(paper_data, "2505.11111", "Scaling Laws for ViTs (v3)")
    waves_paper = paper_from_work(_works()[0], topics=load_topics(), today=TODAY, now=NOW)
    assert waves_paper is not None
    merged = merge.merge([fresh], [waves_paper])
    assert len(merged) == 1
    kept = merged[0]
    assert kept is fresh
    assert kept.funnel == [Funnel.FRESH, Funnel.WAVES]
    assert kept.ids.openalex == "W4500000001"
    assert kept.citations.total == 180
    assert set(kept.topics) == {"graph-theory", "vision", "image-processing"}


def test_merge_by_title_and_author(paper_data: dict[str, Any]) -> None:
    a = _fresh_paper(paper_data, "2410.00001", "Graph Colouring: A New Bound!")
    b = Paper.model_validate(
        {
            **paper_data,
            "id": "doi:10.1/x",
            "ids": {"doi": "10.1/x"},
            "title": "graph colouring a new bound",
            "funnel": ["waves"],
        }
    )
    merged = merge.merge([a], [b])
    assert len(merged) == 1
    assert merged[0].ids.doi == "10.1/x"
    assert merged[0].id == "arxiv:2410.00001"


def test_merge_keeps_distinct_papers(paper_data: dict[str, Any]) -> None:
    a = _fresh_paper(paper_data, "2410.00001", "Graph Colouring: A New Bound")
    b = _fresh_paper(paper_data, "2410.00002", "Graph Colouring: A Different Bound")
    assert len(merge.merge([a, b])) == 2


def test_normalize_title() -> None:
    assert merge.normalize_title("  Café-Style  Graphs: A Survey!") == "cafe style graphs a survey"


def test_fresh_and_waves_share_schema() -> None:
    entries, _, _ = parse_feed((FIXTURES / "arxiv_page.xml").read_bytes())
    fresh = paper_from_entry(entries[0], topics=load_topics(), today=TODAY, now=NOW)
    assert fresh is not None
    Paper.model_validate_json(fresh.model_dump_json())


@respx.mock
def test_pipeline_keeps_fresh_and_waves_papers() -> None:
    from fetcher import pipeline
    from fetcher.sources.arxiv import API_URL as ARXIV_URL
    from fetcher.sources.arxiv import ArxivSource
    from fetcher.sources.semantic_scholar import BATCH_URL, SemanticScholarSource

    respx.get(ARXIV_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "arxiv_page.xml").read_bytes())
    )
    respx.post(BATCH_URL).mock(side_effect=lambda r: httpx.Response(
        200, json=[None] * len(json.loads(r.content)["ids"])
    ))  # fmt: skip
    respx.get(WORKS_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "openalex_waves.json").read_bytes())
    )
    with HttpClient(sleep=lambda _: None) as http:
        candidates = pipeline.run(
            http,
            load_topics(),
            pipeline.RunOptions(today=TODAY, fresh_per_topic=1, waves_per_topic=1),
            now=NOW,
            arxiv=ArxivSource(http, limiter=RateLimiter(0)),
            semantic_scholar=SemanticScholarSource(http, api_key="", limiter=RateLimiter(0)),
            openalex=OpenAlexSource(http, api_key="", limiter=RateLimiter(0)),
            huggingface=HuggingFaceSource(http, limiter=RateLimiter(0)),
            hackernews=HackerNewsSource(http, limiter=RateLimiter(0)),
        )
    ids = {p.id for p in candidates.papers}
    # Both fresh arXiv papers survive even though the waves papers are far more cited
    assert {"arxiv:2610.01234", "arxiv:2610.05678"} <= ids
    assert {"arxiv:2505.11111", "doi:10.1038/s41586-026-00001-x"} <= ids
    waves_report = candidates.sources["openalex_waves"]
    # every configured subfield is queried in two windows, each returning the 3 fixture works
    assert waves_report.fetched == 3 * 2 * len(load_topics().openalex_subfields())
    assert len(ids) == len(candidates.papers)  # no duplicates across queries


def test_malformed_doi_is_dropped_not_fatal() -> None:
    work = _works()[1].model_copy(update={"doi": "https://doi.org/10.1234/has space"})
    paper = paper_from_work(work, topics=load_topics(), today=TODAY, now=NOW)
    assert paper is not None
    assert paper.ids.doi is None
    assert paper.id == "openalex:W4500000002"
