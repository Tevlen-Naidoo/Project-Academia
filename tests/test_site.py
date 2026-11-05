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


def _graph_file(data_dir: Path) -> None:
    from reader.graph import EdgeKind, GraphEdge, GraphFile, GraphNode, NodeRole, PickGraph

    picks = load.load(data_dir).days[0].picks
    pick = picks[0].pick.paper
    nodes = [
        GraphNode(paper=ref_from_paper(pick), role=NodeRole.PICK),
        GraphNode(paper=_ref(3762, "Attention Is All You Need"), role=NodeRole.READ_FIRST),
        GraphNode(paper=_ref(5, "Some <b>other</b> reference"), role=NodeRole.REFERENCE),
        GraphNode(paper=_ref(6, "A later paper"), role=NodeRole.CITING),
    ]
    edges = [
        GraphEdge(source=pick.id, target=nodes[1].paper.id),
        GraphEdge(source=pick.id, target=nodes[2].paper.id),
        GraphEdge(source=nodes[2].paper.id, target=nodes[1].paper.id),
        GraphEdge(source=nodes[3].paper.id, target=pick.id),
        GraphEdge(source=nodes[1].paper.id, target=nodes[2].paper.id, kind=EdgeKind.BUILDS_ON),
    ]
    graphs = GraphFile(
        date=DAY2, generated_at=NOW, graphs=[PickGraph(pick_id=pick.id, nodes=nodes, edges=edges)]
    )
    (data_dir / "graphs").mkdir()
    (data_dir / "graphs" / f"{DAY2}.json").write_text(graphs.model_dump_json(), "utf-8")


def test_graph_page(data_dir: Path, tmp_path: Path) -> None:
    _graph_file(data_dir)
    out = tmp_path / "site"
    pages = _build(data_dir, out)
    graph_page = out / "days" / str(DAY2) / "graph" / "index.html"
    assert graph_page in pages
    text = graph_page.read_text("utf-8")
    assert text.count("<circle") == 4
    assert "Some &lt;b&gt;other&lt;/b&gt; reference" in text
    assert 'class="edge strong builds-on"' in text
    assert "Cited by" in text
    # The pick card links to its graph, and every internal link resolves.
    assert "See connected papers (3)" in (out / "index.html").read_text("utf-8")
    for page in pages:
        for href in HREF.findall(page.read_text("utf-8")):
            href = html.unescape(href)
            if href.startswith(("http://", "https://", "#")):
                continue
            assert (page.parent / href.split("#")[0]).resolve().exists(), href


def test_no_graph_page_without_graphs(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    assert not (out / "days" / str(DAY2) / "graph").exists()
    assert "See connected papers" not in (out / "index.html").read_text("utf-8")


def test_installable_with_notifications(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    manifest = json.loads((out / "manifest.webmanifest").read_text("utf-8"))
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (out / icon["src"]).exists()
    assert (out / "sw.js").exists()
    settings = (out / "settings" / "index.html").read_text("utf-8")
    assert 'id="push-on"' in settings
    assert 'data-push-key=""' in settings  # no key, so notifications stay off

    site = render.Site(
        repo="owner/repo",
        topics=TOPICS,
        data=load.load(data_dir),
        built_at=NOW,
        push_key="BPublicKey",
    )
    render.build(site, out)
    assert 'data-push-key="BPublicKey"' in (out / "index.html").read_text("utf-8")


def test_cli_build_derives_the_push_key(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from notifier import vapid

    private = vapid.generate()
    monkeypatch.setenv(vapid.ENV_VAR, private)
    out = tmp_path / "site"
    assert cli.main(["build", "--data-dir", str(data_dir), "--out", str(out)]) == 0
    public = vapid.public_key(vapid.load_private_key(private))
    assert f'data-push-key="{public}"' in (out / "index.html").read_text("utf-8")

    monkeypatch.setenv(vapid.ENV_VAR, "not a key")
    assert cli.main(["build", "--data-dir", str(data_dir), "--out", str(out)]) == 0
    assert 'data-push-key=""' in (out / "index.html").read_text("utf-8")
