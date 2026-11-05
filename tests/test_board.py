from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
import respx
from pydantic import ValidationError

from fetcher.http import HttpClient, RateLimiter
from fetcher.models import CandidatesFile, Links, PaperIds
from reader import board, cli, finder
from reader.board import (
    TO_READ_LIMIT,
    Board,
    BoardError,
    Card,
    CardSource,
    Column,
    SourceKind,
)
from reader.graph import GraphFile
from reader.lookup import PaperLookup, find_local, parse_ids
from reader.models import PaperRef, PrereqsFile

TODAY = dt.date(2026, 11, 5)
NOW = dt.datetime(2026, 11, 5, 8, 0, tzinfo=dt.UTC)
S2 = "https://api.semanticscholar.org/graph/v1/paper"


def _ref(arxiv: str, title: str = "A paper") -> PaperRef:
    ids = PaperIds(arxiv=arxiv)
    links = Links.model_validate({"abstract_page": f"https://arxiv.org/abs/{arxiv}"})
    return PaperRef(id=ids.canonical(), ids=ids, title=title, links=links)


def _s2_details() -> dict[str, Any]:
    return {
        "paperId": "a" * 40,
        "externalIds": {"ArXiv": "1706.03762", "DOI": "10.48550/arXiv.1706.03762"},
        "title": "Attention Is All You Need",
        "year": 2017,
        "venue": "NeurIPS",
        "publicationTypes": ["Conference"],
        "citationCount": 150000,
        "influentialCitationCount": 20000,
        "tldr": {"model": "tldr@v2.0.0", "text": "Transformers replace recurrence."},
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2410.12345", PaperIds(arxiv="2410.12345")),
        ("2410.12345v3", PaperIds(arxiv="2410.12345")),
        ("arxiv:2410.12345", PaperIds(arxiv="2410.12345")),
        ("https://arxiv.org/abs/2410.12345v2", PaperIds(arxiv="2410.12345")),
        ("https://arxiv.org/pdf/2410.12345.pdf", PaperIds(arxiv="2410.12345")),
        ("arxiv.org/abs/hep-th/9901001", PaperIds(arxiv="hep-th/9901001")),
        ("10.1145/3292500.3330701", PaperIds(doi="10.1145/3292500.3330701")),
        ("https://doi.org/10.1145/3292500.3330701", PaperIds(doi="10.1145/3292500.3330701")),
        ("doi:10.1145/ABC", PaperIds(doi="10.1145/abc")),
        (
            "https://www.semanticscholar.org/paper/Attention-Is-All/" + "b" * 40,
            PaperIds(s2="b" * 40),
        ),
        ("s2:" + "c" * 40, PaperIds(s2="c" * 40)),
        ("https://openalex.org/W2741809807", PaperIds(openalex="W2741809807")),
    ],
)
def test_parse_ids(text: str, expected: PaperIds) -> None:
    assert parse_ids(text) == expected


@pytest.mark.parametrize("text", ["", "attention is all you need", "https://example.com/x"])
def test_parse_ids_rejects(text: str) -> None:
    with pytest.raises(BoardError):
        parse_ids(text)


def test_add_move_and_dates() -> None:
    b = Board()
    b.add(_ref("2410.00001"), today=TODAY)
    ids = PaperIds(arxiv="2410.00001")

    card = b.move(ids, Column.READING, today=TODAY + dt.timedelta(days=1))
    assert (card.started_on, card.finished_on) == (TODAY + dt.timedelta(days=1), None)

    card = b.move(ids, Column.DONE, today=TODAY + dt.timedelta(days=4))
    assert card.started_on == TODAY + dt.timedelta(days=1)
    assert card.finished_on == TODAY + dt.timedelta(days=4)

    card = b.move(ids, Column.TO_READ, today=TODAY)
    assert (card.started_on, card.finished_on) == (None, None)


def test_done_straight_from_to_read_stamps_both_dates() -> None:
    b = Board()
    b.add(_ref("2410.00001"), today=TODAY)
    card = b.move(PaperIds(arxiv="2410.00001"), Column.DONE, today=TODAY)
    assert card.started_on == card.finished_on == TODAY


def test_to_read_limit() -> None:
    b = Board()
    for i in range(TO_READ_LIMIT):
        b.add(_ref(f"2410.{i:05d}"), today=TODAY)
    with pytest.raises(BoardError, match="already holds 10"):
        b.add(_ref("2410.99999"), today=TODAY)
    # Starting one frees a slot.
    b.move(PaperIds(arxiv="2410.00000"), Column.READING, today=TODAY)
    b.add(_ref("2410.99999"), today=TODAY)
    with pytest.raises(BoardError):
        b.move(PaperIds(arxiv="2410.00000"), Column.TO_READ, today=TODAY)


def test_limit_is_enforced_on_load() -> None:
    cards = [Card(paper=_ref(f"2410.{i:05d}"), added_on=TODAY) for i in range(TO_READ_LIMIT + 1)]
    with pytest.raises(ValidationError, match="at most 10"):
        Board(cards=cards)


def test_no_duplicates_and_matching_by_any_id() -> None:
    b = Board()
    ids = PaperIds(arxiv="1706.03762", doi="10.48550/arxiv.1706.03762", s2="a" * 40)
    b.add(
        PaperRef(id=ids.canonical(), ids=ids, title="T", links=_ref("1706.03762").links),
        today=TODAY,
    )
    with pytest.raises(BoardError, match="already on the board, in To read"):
        b.add(_ref("1706.03762"), today=TODAY)
    assert b.find(PaperIds(s2="a" * 40)) is not None
    assert b.find(PaperIds(doi="10.48550/ARXIV.1706.03762")) is not None


def test_paper_ids_cover_every_column_and_id() -> None:
    b = Board()
    b.add(_ref("2410.00001"), today=TODAY)
    ids = PaperIds(arxiv="2410.00002", s2="d" * 40)
    b.add(
        PaperRef(id=ids.canonical(), ids=ids, title="T", links=_ref("2410.00002").links),
        today=TODAY,
    )
    b.move(ids, Column.DONE, today=TODAY)
    assert {"arxiv:2410.00001", "arxiv:2410.00002", "s2:" + "d" * 40} <= b.paper_ids()


def test_card_needs_a_link() -> None:
    ids = PaperIds(doi="10.1/x")
    paper = PaperRef(id=ids.canonical(), ids=ids, title="No links")
    with pytest.raises(ValidationError, match="needs a link"):
        Card(paper=paper, added_on=TODAY)


def test_card_dates_match_column() -> None:
    with pytest.raises(ValidationError):
        Card(paper=_ref("2410.00001"), column=Column.DONE, added_on=TODAY)
    with pytest.raises(ValidationError):
        Card(paper=_ref("2410.00001"), added_on=TODAY, started_on=TODAY)


def test_source_pick_id_only_for_prerequisite() -> None:
    with pytest.raises(ValidationError):
        CardSource(kind=SourceKind.PICK, pick_id="arxiv:2410.00001")


def test_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "data" / "board.json"
    assert board.load(path) == Board()
    b = Board()
    b.add(_ref("2410.00001", "Saved"), today=TODAY)
    board.save(b, path, now=NOW)
    again = board.load(path)
    assert again.cards[0].paper.title == "Saved"
    assert again.updated_at == NOW
    assert path.read_text().endswith("}\n")


def _lookup(tmp_path: Path) -> PaperLookup:
    return PaperLookup(
        HttpClient(attempts=1, sleep=lambda _: None),
        data_dir=tmp_path,
        s2_api_key="",
        limiter=RateLimiter(0),
    )


@respx.mock
def test_lookup_from_s2(tmp_path: Path) -> None:
    route = respx.get(f"{S2}/ARXIV:1706.03762").respond(json=_s2_details())
    ref = _lookup(tmp_path).resolve(PaperIds(arxiv="1706.03762"))
    assert route.called
    assert "tldr" in route.calls[0].request.url.params["fields"]
    assert ref.id == "arxiv:1706.03762"
    assert ref.tldr == "Transformers replace recurrence."
    assert str(ref.links.abstract_page) == "https://arxiv.org/abs/1706.03762"
    assert str(ref.links.pdf) == "https://arxiv.org/pdf/1706.03762"


@respx.mock
def test_lookup_not_found(tmp_path: Path) -> None:
    respx.get(f"{S2}/DOI:10.1/missing").respond(404)
    with pytest.raises(BoardError, match="couldn't look up"):
        _lookup(tmp_path).resolve(PaperIds(doi="10.1/missing"))


def test_lookup_prefers_local_candidates(tmp_path: Path, paper_data: dict[str, Any]) -> None:
    candidates = CandidatesFile.model_validate(
        {"date": "2026-11-03", "generated_at": "2026-11-03T06:00:00Z", "papers": [paper_data]}
    )
    (tmp_path / "candidates").mkdir()
    (tmp_path / "candidates" / "2026-11-03.json").write_text(candidates.model_dump_json())
    ref = find_local(PaperIds(arxiv="2410.12345"), tmp_path)
    assert ref is not None
    assert ref.title == paper_data["title"]
    # No HTTP mock: resolving must not touch the network.
    assert _lookup(tmp_path).resolve(PaperIds(arxiv="2410.12345")).id == "arxiv:2410.12345"


@respx.mock
def test_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    respx.get(f"{S2}/ARXIV:1706.03762").respond(json=_s2_details())
    path = tmp_path / "board.json"
    base = ["board", "--board", str(path), "--data-dir", str(tmp_path), "--today", "2026-11-05"]

    assert cli.main([*base, "add", "https://arxiv.org/abs/1706.03762v7", "--pick"]) == 0
    assert "Added to To read: Attention Is All You Need" in capsys.readouterr().out
    saved = json.loads(path.read_text())
    assert saved["cards"][0]["source"] == {"kind": "pick"}

    assert cli.main([*base, "add", "1706.03762"]) == 1
    assert "already on the board" in capsys.readouterr().err

    assert cli.main([*base, "start", "1706.03762"]) == 0
    assert cli.main([*base, "done", "arxiv:1706.03762"]) == 0
    assert cli.main([*base, "list"]) == 0
    out = capsys.readouterr().out
    assert "Done (1)" in out
    assert "https://arxiv.org/abs/1706.03762" in out

    assert cli.main([*base, "remove", "1706.03762"]) == 0
    assert board.load(path).cards == []


def test_cli_prereq_source(tmp_path: Path, paper_data: dict[str, Any]) -> None:
    candidates = CandidatesFile.model_validate(
        {"date": "2026-11-03", "generated_at": "2026-11-03T06:00:00Z", "papers": [paper_data]}
    )
    (tmp_path / "candidates").mkdir()
    (tmp_path / "candidates" / "2026-11-03.json").write_text(candidates.model_dump_json())
    path = tmp_path / "board.json"
    argv = [
        "board",
        "--board",
        str(path),
        "--data-dir",
        str(tmp_path),
        "add",
        "2410.12345",
        "--prereq-of",
        "arxiv:2411.00001",
        "--date-found",
        "2026-11-04",
    ]
    assert cli.main(argv) == 0
    source = board.load(path).cards[0].source
    assert source.kind is SourceKind.PREREQUISITE
    assert source.pick_id == "arxiv:2411.00001"
    assert source.date == dt.date(2026, 11, 4)


def test_prereqs_skip_board_papers(
    tmp_path: Path, paper_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = CandidatesFile.model_validate(
        {"date": "2026-11-05", "generated_at": "2026-11-05T06:00:00Z", "papers": [paper_data]}
    )
    file = tmp_path / "candidates.json"
    file.write_text(candidates.model_dump_json())
    b = Board()
    b.add(_ref("1706.03762"), today=TODAY)
    b.move(PaperIds(arxiv="1706.03762"), Column.DONE, today=TODAY)
    board.save(b, tmp_path / "board.json", now=NOW)

    seen: dict[str, set[str]] = {}

    def fake_run(
        _http: object, _picks: object, options: finder.FinderOptions
    ) -> tuple[PrereqsFile, GraphFile]:
        seen["exclude"] = options.exclude
        return PrereqsFile(date=TODAY, generated_at=NOW), GraphFile(date=TODAY, generated_at=NOW)

    monkeypatch.setattr(finder, "run", fake_run)
    argv = ["prereqs", str(file), "--date", "2026-11-05", "--data-dir", str(tmp_path)]
    assert cli.main(argv) == 0
    assert "arxiv:1706.03762" in seen["exclude"]
