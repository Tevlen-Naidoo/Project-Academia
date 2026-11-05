from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any

import pytest

from fetcher.models import Paper
from fetcher.topics import load_topics
from reader import board
from reader.lookup import ref_from_paper
from reader.models import (
    PaperRef,
    PickPrereqs,
    PrereqLabel,
    PrereqsFile,
    Prerequisite,
    ReferenceSource,
)
from scorer import pipeline
from scorer.config import load_scoring
from site_builder import cli, load, render
from tests.test_scorer import NOW, _pool, _write_candidates, make_paper

DAY1 = dt.date(2026, 11, 4)
DAY2 = dt.date(2026, 11, 5)
TOPICS = load_topics()
HREF = re.compile(r'(?:href|src)="([^"]+)"')


def _picks(data_dir: Path, day: dt.date, extra: list[Paper] | None = None) -> list[Paper]:
    source = _write_candidates(data_dir, day, [*_pool(day), *(extra or [])])
    picks = pipeline.run(source, config=load_scoring(), topics=TOPICS, data_dir=data_dir, now=NOW)
    pipeline.write(picks, data_dir)
    return [p.paper for p in picks.picks]


def _ref(n: int, title: str) -> PaperRef:
    return PaperRef.model_validate(
        {
            "id": f"arxiv:1706.{n:05d}",
            "ids": {"arxiv": f"1706.{n:05d}"},
            "title": title,
            "year": 2017,
            "links": {"abstract_page": f"https://arxiv.org/abs/1706.{n:05d}"},
        }
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    _picks(data, DAY1)
    risky = make_paper(99_000, topics=("vision",), upvotes=10_000, submitted=DAY2).model_copy(
        update={"title": "Breaking <script>alert(1)</script> & co"}
    )
    picks = _picks(data, DAY2, [risky])

    prereq = Prerequisite(
        paper=_ref(3762, "Attention Is All You Need"),
        label=PrereqLabel.READ_FIRST,
        score=0.9,
        reasons=["Influential reference"],
        supports=[picks[0].id],
    )
    prereqs = PrereqsFile(
        date=DAY2,
        generated_at=NOW,
        picks=[
            PickPrereqs(
                pick_id=picks[0].id,
                pick_title=picks[0].title,
                source=ReferenceSource.S2,
                prerequisites=[prereq],
            )
        ],
    )
    (data / "prereqs").mkdir()
    (data / "prereqs" / f"{DAY2}.json").write_text(prereqs.model_dump_json(), "utf-8")

    b = board.Board()
    b.add(ref_from_paper(picks[1]), today=DAY2)
    b.add(_ref(1, "An older paper"), today=DAY1)
    b.move(_ref(1, "x").ids, board.Column.DONE, today=DAY2)
    board.save(b, data / "board.json", now=NOW)
    return data


def _build(data_dir: Path, out: Path) -> list[Path]:
    site = render.Site(repo="owner/repo", topics=TOPICS, data=load.load(data_dir), built_at=NOW)
    return render.build(site, out)


def test_every_page_and_internal_link_exists(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    pages = _build(data_dir, out)
    names = {p.relative_to(out).as_posix() for p in pages}
    assert names == {
        "index.html",
        f"days/{DAY1}/index.html",
        f"days/{DAY2}/index.html",
        "archive/index.html",
        "board/index.html",
        "settings/index.html",
    }
    for page in pages:
        for href in HREF.findall(page.read_text("utf-8")):
            href = html.unescape(href)
            if href.startswith(("http://", "https://", "#")):
                continue
            target = (page.parent / href.split("#")[0]).resolve()
            assert target.exists(), f"{page.relative_to(out)} links to missing {href}"


def test_today_shows_the_latest_day(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    index = (out / "index.html").read_text("utf-8")
    assert "Thursday 5 November 2026" in index
    assert f"days/{DAY1}/index.html" in index  # link to the day before
    assert index.count('<article class="pick"') == 3
    assert "Attention Is All You Need" in index
    assert "Read before this (1)" in index


def test_titles_are_escaped(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    index = (out / "index.html").read_text("utf-8")
    assert "<script>alert(1)</script>" not in index
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in index


def _payloads(page: str) -> list[dict[str, Any]]:
    return [json.loads(html.unescape(m)) for m in re.findall(r"data-card='([^']*)'", page)]


def test_board_buttons_carry_valid_cards(data_dir: Path, tmp_path: Path) -> None:
    """What board.js saves must load with the board model the scorer uses."""
    out = tmp_path / "site"
    _build(data_dir, out)
    payloads = _payloads((out / "index.html").read_text("utf-8"))
    assert len(payloads) == 4  # 3 picks + 1 prerequisite
    kinds = set()
    for payload in payloads:
        card = board.Card.model_validate({**payload, "column": "to_read", "added_on": "2026-11-05"})
        kinds.add(card.source.kind)
        assert card.source.date == DAY2
    assert kinds == {board.SourceKind.PICK, board.SourceKind.PREREQUISITE}


def test_papers_already_on_the_board_say_so(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    index = (out / "index.html").read_text("utf-8")
    assert "On your reading board: <span>To read</span>" in index
    board_page = (out / "board" / "index.html").read_text("utf-8")
    assert "An older paper" in board_page
    assert "1/10" in board_page


def test_empty_data_still_builds(tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(tmp_path / "nothing", out)
    assert "No papers yet" in (out / "index.html").read_text("utf-8")
    assert (out / "static" / "board.js").exists()
    assert (out / ".nojekyll").exists()


def test_cli_build(data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "site"
    assert cli.main(["build", "--data-dir", str(data_dir), "--out", str(out)]) == 0
    assert "2 days" in capsys.readouterr().out
    assert (out / "archive" / "index.html").exists()


def test_repo_is_written_for_board_js(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    assert 'data-repo="owner/repo"' in (out / "settings" / "index.html").read_text("utf-8")
