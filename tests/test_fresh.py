from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
import respx

from fetcher import pipeline
from fetcher.funnels.fresh import paper_from_entry, venue_from_comment
from fetcher.http import HttpClient, RateLimiter
from fetcher.models import CandidatesFile, PublicationStatus, StatusEvidence
from fetcher.sources.arxiv import API_URL, ArxivSource, parse_feed
from fetcher.topics import load_topics

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 3)
NOW = dt.datetime(2026, 11, 3, 6, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("comment", "venue"),
    [
        ("Accepted at CVPR 2026. 14 pages", "CVPR 2026"),
        ("accepted to NeurIPS 2026 (spotlight)", "NeurIPS 2026"),
        ("To appear in the Journal of Graph Theory", "Journal of Graph Theory"),
        ("Camera-ready version for ICML '26", "ICML '26"),
        ("12 pages, 4 figures", None),
        ("Under review", None),
        (None, None),
    ],
)
def test_venue_from_comment(comment: str | None, venue: str | None) -> None:
    assert venue_from_comment(comment) == venue


def test_paper_from_entry() -> None:
    entries, _, _ = parse_feed((FIXTURES / "arxiv_page.xml").read_bytes())
    topics = load_topics()
    papers = [paper_from_entry(e, topics=topics, today=TODAY, now=NOW) for e in entries]

    restoration, colouring, partitions = papers
    assert restoration is not None
    assert restoration.id == "arxiv:2610.01234"
    assert restoration.topics == ["vision", "image-processing"]
    assert restoration.publication.status is PublicationStatus.ACCEPTED
    assert restoration.publication.venue == "CVPR 2026"
    assert str(restoration.links.abstract_page) == "https://arxiv.org/abs/2610.01234"
    assert restoration.dates.submitted == dt.date(2026, 10, 29)

    assert colouring is not None
    assert colouring.topics == ["graph-theory"]
    assert colouring.publication.status is PublicationStatus.PUBLISHED
    assert colouring.publication.evidence is StatusEvidence.ARXIV_JOURNAL_REF
    assert colouring.ids.doi == "10.1002/jgt.99999"
    assert str(colouring.links.doi) == "https://doi.org/10.1002/jgt.99999"

    # math.CO without graph keywords is not in any topic
    assert partitions is None


@respx.mock
def test_pipeline_run_writes_valid_file(tmp_path: Path) -> None:
    respx.get(API_URL).mock(
        return_value=httpx.Response(200, content=(FIXTURES / "arxiv_page.xml").read_bytes())
    )
    with HttpClient(sleep=lambda _: None) as http:
        candidates = pipeline.run(
            http,
            load_topics(),
            pipeline.RunOptions(today=TODAY),
            now=NOW,
            arxiv=ArxivSource(http, limiter=RateLimiter(0)),
        )
    path = pipeline.write(candidates, tmp_path)

    assert path == tmp_path / "candidates" / "2026-11-03.json"
    loaded = CandidatesFile.model_validate_json(path.read_bytes())
    assert [p.id for p in loaded.papers] == ["arxiv:2610.01234", "arxiv:2610.05678"]
    report = loaded.sources["arxiv"]
    assert report.fetched == 3
    assert any("2610.04444" in e for e in report.errors)
    assert json.loads(path.read_text())["schema_version"] == 1


@respx.mock
def test_pipeline_survives_source_outage() -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(503))
    with HttpClient(attempts=2, sleep=lambda _: None) as http:
        candidates = pipeline.run(
            http,
            load_topics(),
            pipeline.RunOptions(today=TODAY),
            now=NOW,
            arxiv=ArxivSource(http, limiter=RateLimiter(0)),
        )
    assert candidates.papers == []
    assert candidates.sources["arxiv"].errors


def test_fresh_window() -> None:
    since, until = pipeline.fresh_window(TODAY, 7)
    assert until == dt.datetime(2026, 11, 3, tzinfo=dt.UTC)
    assert since == dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
