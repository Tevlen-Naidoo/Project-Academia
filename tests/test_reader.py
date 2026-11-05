from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fetcher.http import HttpClient, RateLimiter
from fetcher.models import Paper, PaperIds
from fetcher.sources.openalex import WORKS_URL
from fetcher.sources.semantic_scholar import SemanticScholarSource
from reader import cli, finder, rank, schema
from reader.models import PaperRef, PrereqLabel, PrereqsFile, ReferenceSource
from reader.references import (
    OARefWork,
    Reference,
    ReferenceClient,
    S2Reference,
    ref_from_openalex,
    ref_from_s2,
)

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 5)
NOW = dt.datetime(2026, 11, 5, 6, 0, tzinfo=dt.UTC)
S2 = "https://api.semanticscholar.org/graph/v1/paper"


def _refs_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "s2_references.json").read_text())
    return data


def _ref(
    pid: str,
    *,
    citations: int,
    influential: bool | None = True,
    intents: list[str] | None = None,
    review: bool = False,
) -> Reference:
    ids = PaperIds(s2=pid)
    paper = PaperRef(id=ids.canonical(), ids=ids, title=pid, citations=citations, is_review=review)
    return Reference(
        paper=paper,
        influential=influential,
        intents=["methodology"] if intents is None else intents,
    )


def _client() -> ReferenceClient:
    return ReferenceClient(
        HttpClient(attempts=1, sleep=lambda _: None),
        s2_api_key="",
        openalex_api_key="",
        s2_limiter=RateLimiter(0),
        oa_limiter=RateLimiter(0),
    )


def _pick(paper_data: dict[str, Any]) -> Paper:
    return Paper.model_validate(paper_data)


def _mock_chain(router: respx.MockRouter) -> None:
    router.get(f"{S2}/ARXIV:2410.12345/references").respond(json=_refs_json())
    # Attention -> a deeper method paper -> nothing qualifying: that one is the Foundation.
    router.get(f"{S2}/sA/references").respond(
        json={
            "data": [
                {
                    "isInfluential": True,
                    "intents": ["methodology"],
                    "contexts": [],
                    "citedPaper": {
                        "paperId": "sA1",
                        "title": "Neural Machine Translation by Jointly "
                        "Learning to Align and Translate",
                        "year": 2014,
                        "externalIds": {"ArXiv": "1409.0473"},
                        "citationCount": 30000,
                    },
                },
                {
                    "isInfluential": False,
                    "intents": ["background"],
                    "contexts": [],
                    "citedPaper": {"paperId": "sX", "title": "Unrelated", "citationCount": 900},
                },
            ]
        }
    )
    router.get(f"{S2}/sA1/references").respond(
        json={
            "data": [
                {
                    "isInfluential": False,
                    "intents": ["background"],
                    "contexts": [],
                    "citedPaper": {"paperId": "sY", "title": "Old background", "citationCount": 50},
                },
            ]
        }
    )
    router.get(f"{S2}/sB/references").respond(json={"data": []})

    def batch(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["ids"]
        tldr = {"text": "Transformers replace recurrence with attention."}
        return httpx.Response(
            200, json=[{"paperId": i, "tldr": tldr} if i == "sA" else None for i in ids]
        )

    router.post(f"{S2}/batch").mock(side_effect=batch)


def test_rank_labels_and_limits() -> None:
    refs: list[Reference] = []
    for row in _refs_json()["data"]:
        item = S2Reference.model_validate(row)
        paper = ref_from_s2(item.cited_paper)
        if paper:
            refs.append(
                Reference(
                    paper=paper,
                    influential=item.is_influential,
                    intents=item.intents,
                    contexts=item.contexts or [],
                )
            )
    assert len(refs) == 6  # the unresolved reference is dropped
    prereqs, read_first = rank.select("arxiv:2410.12345", refs)
    labels = [(p.paper.title, p.label) for p in prereqs]
    assert labels[:2] == [
        ("Attention Is All You Need", PrereqLabel.READ_FIRST),
        ("Deep Residual Learning", PrereqLabel.READ_FIRST),
    ]
    background = [t for t, label in labels if label is PrereqLabel.BACKGROUND]
    # The third method paper falls to Background; one survey only; too few citations is out.
    assert background == ["A Smaller Method Paper", "A Survey of Vision Transformers"]
    assert prereqs[0].quote == "We build on the transformer architecture [12]."
    assert "Influential reference for this paper" in prereqs[0].reasons
    assert "52k citations" in prereqs[0].reasons
    assert [r.paper.id for r in read_first] == ["arxiv:1706.03762", "doi:10.1109/cvpr.2016.90"]


def test_unprocessed_list_needs_more_citations() -> None:
    refs = [
        _ref("a", citations=500, influential=False),
        _ref("b", citations=2000, influential=False),
    ]
    _, read_first = rank.select("s2:pick", refs)
    assert [r.paper.title for r in read_first] == ["b"]


def test_openalex_refs_are_never_read_first() -> None:
    refs = [_ref("a", citations=90000, influential=None, intents=None)]
    prereqs, read_first = rank.select("s2:pick", refs)
    assert read_first == []
    assert [p.label for p in prereqs] == [PrereqLabel.BACKGROUND]


def test_excluded_and_shared() -> None:
    refs = [_ref("a", citations=500), _ref("b", citations=400)]
    prereqs, _ = rank.select("s2:pick", refs, shared={"s2:b": 3}, exclude={"s2:a"})
    assert [p.paper.id for p in prereqs] == ["s2:b"]
    assert "Also a reference of 2 other picks" in prereqs[0].reasons


@respx.mock
def test_find_with_foundation(paper_data: dict[str, Any]) -> None:
    _mock_chain(respx.mock)
    http = HttpClient(attempts=1, sleep=lambda _: None)
    s2 = SemanticScholarSource(http, api_key="", limiter=RateLimiter(0))
    out = finder.find(
        [_pick(paper_data)], _client(), finder.FinderOptions(today=TODAY), s2=s2, now=NOW
    )
    pick = out.picks[0]
    assert pick.source is ReferenceSource.S2
    assert pick.references_seen == 6
    foundations = [p for p in pick.prerequisites if p.label is PrereqLabel.FOUNDATION]
    assert len(foundations) == 1
    found = foundations[0]
    assert found.paper.id == "arxiv:1409.0473"
    assert found.chain == ["arxiv:1706.03762", "arxiv:1409.0473"]
    assert found.reasons[0] == "Foundation under “Attention Is All You Need”"
    assert pick.prerequisites[0].paper.tldr == "Transformers replace recurrence with attention."
    # round-trips through JSON
    PrereqsFile.model_validate_json(out.model_dump_json(exclude_none=True))


@respx.mock
def test_openalex_fallback(paper_data: dict[str, Any]) -> None:
    respx.get(f"{S2}/ARXIV:2410.12345/references").respond(404)
    respx.get(f"{WORKS_URL}/doi:10.48550/arXiv.2410.12345").respond(
        json={"id": "https://openalex.org/W1", "referenced_works": ["https://openalex.org/W9"]}
    )
    respx.get(WORKS_URL).respond(
        json={
            "results": [
                {
                    "id": "https://openalex.org/W9",
                    "doi": "https://doi.org/10.1000/xyz",
                    "display_name": "An Old Classic",
                    "publication_year": 1998,
                    "cited_by_count": 4000,
                    "type": "article",
                    "primary_location": {"source": {"display_name": "Nature"}},
                },
            ]
        }
    )
    out = finder.find([_pick(paper_data)], _client(), finder.FinderOptions(today=TODAY), now=NOW)
    pick = out.picks[0]
    assert pick.source is ReferenceSource.OPENALEX
    assert [(p.paper.id, p.label) for p in pick.prerequisites] == [
        ("doi:10.1000/xyz", PrereqLabel.BACKGROUND)
    ]
    assert pick.errors and "404" in pick.errors[0]


def test_ref_from_openalex_arxiv() -> None:
    work = OARefWork.model_validate(
        {
            "id": "https://openalex.org/W5",
            "doi": "https://doi.org/10.48550/arXiv.1706.03762",
            "display_name": "Attention Is All You Need",
        }
    )
    ref = ref_from_openalex(work)
    assert ref is not None and ref.id == "arxiv:1706.03762"


def test_history_and_write(tmp_path: Path) -> None:
    ids = PaperIds(s2="old")
    data = {
        "date": "2026-11-01",
        "generated_at": "2026-11-01T06:00:00Z",
        "picks": [
            {
                "pick_id": "s2:pick",
                "pick_title": "P",
                "source": "s2",
                "prerequisites": [
                    {
                        "paper": {
                            "id": "s2:old",
                            "ids": ids.model_dump(exclude_none=True),
                            "title": "Old",
                        },
                        "label": "background",
                        "score": 1.0,
                        "supports": ["s2:pick"],
                    }
                ],
            }
        ],
    }
    old = PrereqsFile.model_validate(data)
    finder.write(old, tmp_path)
    assert finder.history(tmp_path, TODAY) == {"s2:pick", "s2:old"}
    assert finder.history(tmp_path, dt.date(2026, 12, 15)) == set()


def test_foundation_requires_chain() -> None:
    ids = PaperIds(s2="x")
    with pytest.raises(ValueError):
        PrereqsFile.model_validate(
            {
                "date": "2026-11-01",
                "generated_at": "2026-11-01T06:00:00Z",
                "picks": [
                    {
                        "pick_id": "s2:p",
                        "pick_title": "P",
                        "source": "s2",
                        "prerequisites": [
                            {
                                "paper": {
                                    "id": "s2:x",
                                    "ids": ids.model_dump(exclude_none=True),
                                    "title": "X",
                                },
                                "label": "foundation",
                                "score": 1,
                                "supports": ["s2:p"],
                            }
                        ],
                    }
                ],
            }
        )


def test_choose_picks(paper_data: dict[str, Any]) -> None:
    pick = _pick(paper_data)
    assert cli.choose_picks([pick], [], 3) == [pick]
    assert cli.choose_picks([pick], ["arxiv:2410.12345"], 3) == [pick]
    with pytest.raises(SystemExit):
        cli.choose_picks([pick], ["s2:nope"], 3)


def test_schema_up_to_date() -> None:
    assert schema.stale() == []


def test_load_papers_from_picks_file(tmp_path: Path, paper_data: dict[str, Any]) -> None:
    second = {**paper_data, "id": "arxiv:2410.99999", "ids": {"arxiv": "2410.99999"}}
    picks = {
        "date": "2026-11-05",
        "picks": [{"rank": 2, "paper": second}, {"rank": 1, "paper": paper_data}],
    }
    path = tmp_path / "picks.json"
    path.write_text(json.dumps(picks))
    papers, ranked = cli.load_papers(path)
    assert ranked
    assert [p.id for p in papers] == ["arxiv:2410.12345", "arxiv:2410.99999"]
    assert [p.id for p in cli.choose_picks(papers, [], 1, ranked=True)] == ["arxiv:2410.12345"]


def test_load_papers_from_candidates_file(tmp_path: Path, paper_data: dict[str, Any]) -> None:
    candidates = {
        "date": "2026-11-05",
        "generated_at": "2026-11-05T06:00:00Z",
        "papers": [paper_data],
    }
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(candidates))
    papers, ranked = cli.load_papers(path)
    assert not ranked
    assert [p.id for p in papers] == ["arxiv:2410.12345"]


def _fielded(pid: str, fields: set[str], citations: int = 50_000) -> Reference:
    ref = _ref(pid, citations=citations, influential=False, intents=[])
    ref.fields = frozenset(fields)
    return ref


def test_background_must_share_the_picks_field() -> None:
    # The first live run: a CS pick got DESeq2 and a medical review as Background.
    refs = [
        _fielded("deseq2", {"Biology"}),
        _fielded("amd", {"Medicine"}),
        _fielded("dirstats", {"Mathematics"}),
        _fielded("react", {"Computer Science"}, citations=13_000),
        _fielded("unknown", set()),
    ]
    cs = frozenset({"Computer Science", "Engineering"})
    prereqs, _ = rank.select("s2:pick", refs, pick_fields=cs)
    assert [p.paper.title for p in prereqs] == ["react"]
    # Without known pick fields nothing is filtered.
    prereqs, _ = rank.select("s2:pick", refs)
    assert len(prereqs) == 3


def test_direct_signal_overrides_field() -> None:
    ref = _fielded("method", {"Biology"})
    ref.intents = ["methodology"]
    assert rank.on_topic(ref, frozenset({"Computer Science"}))


def test_reference_fields_from_s2_and_openalex() -> None:
    item = S2Reference.model_validate(
        {
            "citedPaper": {
                "paperId": "x",
                "title": "T",
                "fieldsOfStudy": ["Medicine"],
                "s2FieldsOfStudy": [{"category": "Biology", "source": "s2-fos-model"}],
            }
        }
    )
    assert item.cited_paper.fields() == {"Medicine", "Biology"}
    work = OARefWork.model_validate(
        {
            "id": "https://openalex.org/W1",
            "display_name": "T",
            "primary_topic": {"field": {"display_name": "Physics and Astronomy"}},
        }
    )
    assert work.fields() == {"Physics"}


def test_pick_fields(paper_data: dict[str, Any]) -> None:
    from fetcher.topics import load_topics
    from reader.fields import pick_fields

    topics = load_topics()
    cs_pick = _pick({**paper_data, "topics": ["ml", "vision"], "source_categories": ["cs.CV"]})
    assert pick_fields(cs_pick, topics) == {"Computer Science", "Engineering"}
    graph = _pick({**paper_data, "topics": ["graph-theory"], "source_categories": ["math.CO"]})
    assert "Mathematics" in pick_fields(graph, topics)
    astro = _pick({**paper_data, "topics": ["astronomy"], "source_categories": ["astro-ph.GA"]})
    assert pick_fields(astro, topics) == {"Physics"}
