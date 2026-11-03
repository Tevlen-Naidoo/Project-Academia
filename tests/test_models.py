from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from fetcher.models import (
    CandidatesFile,
    Paper,
    PaperIds,
    Publication,
    PublicationStatus,
    StatusEvidence,
)


def test_minimal_paper_is_valid(paper_data: dict[str, Any]) -> None:
    paper = Paper.model_validate(paper_data)
    assert paper.publication.status is PublicationStatus.PREPRINT
    assert paper.citations.total is None  # unknown, not zero
    assert paper.buzz.awards == []


def test_paper_round_trips_through_json(paper_data: dict[str, Any]) -> None:
    paper = Paper.model_validate(paper_data)
    again = Paper.model_validate_json(paper.model_dump_json())
    assert again == paper


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        ({"arxiv": "2410.12345", "doi": "10.1/X"}, "arxiv:2410.12345"),
        ({"arxiv": "hep-th/9901001"}, "arxiv:hep-th/9901001"),
        ({"doi": "10.1145/ABC.123", "openalex": "W1"}, "doi:10.1145/abc.123"),
        ({"openalex": "W42", "s2": "abc"}, "openalex:W42"),
        ({"s2": "abc"}, "s2:abc"),
    ],
)
def test_canonical_id_priority(ids: dict[str, str], expected: str) -> None:
    assert PaperIds.model_validate(ids).canonical() == expected


def test_ids_cannot_all_be_missing() -> None:
    with pytest.raises(ValidationError, match="at least one id"):
        PaperIds()


def test_id_must_be_canonical(paper_data: dict[str, Any]) -> None:
    paper_data["id"] = "doi:10.48550/arxiv.2410.12345"
    with pytest.raises(ValidationError, match="canonical id"):
        Paper.model_validate(paper_data)


@pytest.mark.parametrize("bad_arxiv", ["2410.12345v2", "arXiv:2410.12345", "1234"])
def test_arxiv_id_is_bare_and_unversioned(bad_arxiv: str) -> None:
    with pytest.raises(ValidationError):
        PaperIds(arxiv=bad_arxiv)


def test_published_needs_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        Publication(status=PublicationStatus.PUBLISHED)
    with pytest.raises(ValidationError, match="evidence"):
        Publication(evidence=StatusEvidence.S2_VENUE)
    ok = Publication(status=PublicationStatus.PUBLISHED, evidence=StatusEvidence.S2_VENUE)
    assert ok.venue is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("abstract",), ""),
        (("funnel",), []),
        (("topics",), ["Not A Tag"]),
        (("fetched_at",), "2026-11-03T06:00:00"),  # naive datetime
        (("citations",), {"total": -1}),
        (("unexpected",), 1),
    ],
)
def test_rejects_bad_fields(paper_data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    data = copy.deepcopy(paper_data)
    data[path[0]] = value
    with pytest.raises(ValidationError):
        Paper.model_validate(data)


def test_candidates_file_rejects_duplicates(paper_data: dict[str, Any]) -> None:
    payload = {
        "date": "2026-11-03",
        "generated_at": "2026-11-03T06:05:00Z",
        "papers": [paper_data, paper_data],
    }
    with pytest.raises(ValidationError, match="duplicate"):
        CandidatesFile.model_validate(payload)
