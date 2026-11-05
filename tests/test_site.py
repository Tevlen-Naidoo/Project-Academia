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
STATIC = Path(render.__file__).parent / "static"


def _attr(page: str, element: str, name: str) -> list[Any]:
    """The JSON a page hands one of its web components, e.g. every <paper-card pick>."""
    pattern = rf"<{element}\b[^>]*\b{name}='([^']*)'"
    return [json.loads(html.unescape(m)) for m in re.findall(pattern, page)]


def _json_hrefs(value: Any) -> list[str]:
    """Every link inside a component's JSON."""
    if isinstance(value, dict):
        found = [v for k, v in value.items() if k.endswith("href") and isinstance(v, str)]
        return found + [h for v in value.values() for h in _json_hrefs(v)]
    if isinstance(value, list):
        return [h for v in value for h in _json_hrefs(v)]
    return []


def _internal_links(page: Path) -> list[str]:
    text = page.read_text("utf-8")
    links = [html.unescape(h) for h in HREF.findall(text)]
    for element, name in [
        ("paper-card", "pick"),
        ("archive-list", "days"),
        ("paper-graph", "layout"),
    ]:
        links += _json_hrefs(_attr(text, element, name))
    links += [html.unescape(h) for h in re.findall(r'(?:older|newer)-href="([^"]+)"', text)]
    return [h for h in links if h and not h.startswith(("http://", "https://", "#"))]


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
        for href in _internal_links(page):
            target = (page.parent / href.split("#")[0]).resolve()
            assert target.exists(), f"{page.relative_to(out)} links to missing {href}"


def test_every_component_is_defined(data_dir: Path, tmp_path: Path) -> None:
    """Each custom element a page uses has a customElements.define in the static JS."""
    out = tmp_path / "site"
    pages = _build(data_dir, out)
    defined = set()
    for script in STATIC.glob("js/**/*.js"):
        defined |= set(
            re.findall(r'customElements\.define\("([a-z-]+)"', script.read_text("utf-8"))
        )
    used = {
        tag for page in pages for tag in re.findall(r"<([a-z]+-[a-z-]+)", page.read_text("utf-8"))
    }
    assert used <= defined, used - defined
    assert {"reading-board", "paper-card", "site-header", "push-settings"} <= used
    app = (STATIC / "js" / "app.js").read_text("utf-8")
    for script in STATIC.glob("js/components/*.js"):
        assert f"./components/{script.name}" in app


def test_today_shows_the_latest_day(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    index = (out / "index.html").read_text("utf-8")
    assert "Thursday 5 November 2026" in index
    assert f"days/{DAY1}/index.html" in index  # link to the day before
    picks = _attr(index, "paper-card", "pick")
    assert len(picks) == 3
    assert [p["rank"] for p in picks] == [1, 2, 3]
    prereqs = [pre for p in picks for pre in p["prerequisites"]]
    assert [pre["title"] for pre in prereqs] == ["Attention Is All You Need"]


def test_titles_are_escaped(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    index = (out / "index.html").read_text("utf-8")
    assert "<script>alert(1)</script>" not in index
    titles = [p["title"] for p in _attr(index, "paper-card", "pick")]
    assert "Breaking <script>alert(1)</script> & co" in titles


def _payloads(page: str) -> list[dict[str, Any]]:
    """The cards <board-button> would add, for every pick and prerequisite."""
    buttons = []
    for pick in _attr(page, "paper-card", "pick"):
        buttons.append(pick["board"])
        buttons += [pre["board"] for pre in pick["prerequisites"]]
    return [b["payload"] for b in buttons]


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
    columns = [p["board"]["column"] for p in _attr(index, "paper-card", "pick")]
    assert sorted(columns) == ["", "", "to_read"]
    board_page = (out / "board" / "index.html").read_text("utf-8")
    (built,) = _attr(board_page, "reading-board", "board")
    loaded = board.Board.model_validate(built)  # the same shape board.js reads from GitHub
    assert [c.paper.title for c in loaded.column(board.Column.DONE)] == ["An older paper"]
    assert len(loaded.column(board.Column.TO_READ)) == 1


def test_empty_data_still_builds(tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(tmp_path / "nothing", out)
    assert "No papers yet" in (out / "index.html").read_text("utf-8")
    assert (out / "static" / "js" / "app.js").exists()
    assert (out / "static" / "vendor" / "lit.js").exists()
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
    assert "<b>other</b>" not in text
    (lay,) = _attr(text, "paper-graph", "layout")
    assert len(lay["nodes"]) == 4
    assert "Some <b>other</b> reference" in {n["title"] for n in lay["nodes"]}
    assert [e for e in lay["edges"] if e["strong"] and e["builds_on"]]
    ids = {n["id"] for n in lay["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in lay["edges"])
    assert "Cited by" in [t["label"] for t in lay["ticks"]]
    # The pick card links to its graph, and every internal link resolves.
    index = (out / "index.html").read_text("utf-8")
    graphs = [p["graph"] for p in _attr(index, "paper-card", "pick") if p["graph"]]
    assert [g["count"] for g in graphs] == [3]
    for page in pages:
        for href in _internal_links(page):
            assert (page.parent / href.split("#")[0]).resolve().exists(), href


def test_no_graph_page_without_graphs(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    assert not (out / "days" / str(DAY2) / "graph").exists()
    index = (out / "index.html").read_text("utf-8")
    assert all(p["graph"] is None for p in _attr(index, "paper-card", "pick"))


def test_installable_with_notifications(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    _build(data_dir, out)
    manifest = json.loads((out / "manifest.webmanifest").read_text("utf-8"))
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (out / icon["src"]).exists()
    assert (out / "sw.js").exists()
    settings = (out / "settings" / "index.html").read_text("utf-8")
    assert "<push-settings>" in settings
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


@pytest.mark.parametrize(
    "raw",
    [
        '{"apiKey": "k", "authDomain": "a.firebaseapp.com", "projectId": "a", '
        '"appId": "1:2:web:3"}',
        """const firebaseConfig = {
  apiKey: "k",
  authDomain: "a.firebaseapp.com",
  projectId: "a",
  storageBucket: "a.appspot.com",
  appId: "1:2:web:3",
};""",
    ],
)
def test_firebase_config_reaches_every_page(
    raw: str, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.FIREBASE_ENV, raw)
    config = cli.firebase_config()
    assert json.loads(config)["projectId"] == "a"
    site = render.Site(
        repo="owner/repo",
        topics=TOPICS,
        data=load.load(data_dir),
        built_at=NOW,
        firebase_config=config,
    )
    out = tmp_path / "site"
    render.build(site, out)
    page = (out / "settings" / "index.html").read_text("utf-8")
    match = re.search(r'data-firebase="([^"]*)"', page)
    assert match is not None
    assert json.loads(html.unescape(match.group(1)))["apiKey"] == "k"
    assert "<account-settings>" in page


def test_bad_firebase_config_is_ignored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(cli.FIREBASE_ENV, '{"apiKey": "k"}')
    assert cli.firebase_config() == ""
    assert "Sign-in stays off" in capsys.readouterr().out
    monkeypatch.delenv(cli.FIREBASE_ENV)
    assert cli.firebase_config() == ""
