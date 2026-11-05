"""Render the site into a folder of static HTML, CSS and a little JavaScript."""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from fetcher.models import Paper, PaperIds
from fetcher.topics import TopicsConfig
from reader import board
from reader.lookup import ref_from_paper
from reader.models import PaperRef
from site_builder import graph_view
from site_builder.load import Day, SiteData

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
DEFAULT_REPO = "Tevlen-Naidoo/Project-Academia"
DONE_SHOWN = 20
"""Done cards shown open on the board page; older ones fold away."""


@dataclass(frozen=True, slots=True)
class Site:
    repo: str
    topics: TopicsConfig
    data: SiteData
    built_at: dt.datetime


def _environment(site: Site) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    def topic_label(tag: str | None) -> str:
        if tag is None:
            return "Other"
        topic = site.topics.topics.get(tag)
        return topic.label if topic else tag

    def long_date(day: dt.date) -> str:
        return f"{day:%A} {day.day} {day:%B %Y}"

    def card_for(ids: PaperIds) -> board.Card | None:
        return site.data.board.find(ids)

    def authors(paper: Paper, limit: int = 6) -> str:
        names = [a.name for a in paper.authors]
        if len(names) > limit:
            return ", ".join(names[:limit]) + f" and {len(names) - limit} more"
        return ", ".join(names)

    def card_payload(
        paper: Paper | PaperRef, *, found: dt.date, prereq_of: str | None = None
    ) -> dict[str, Any]:
        """What board.js puts on the board for this paper: a `Card` without its dates."""
        ref = ref_from_paper(paper) if isinstance(paper, Paper) else paper
        source = board.CardSource(
            kind=board.SourceKind.PREREQUISITE if prereq_of else board.SourceKind.PICK,
            date=found,
            pick_id=prereq_of,
        )
        return {
            "paper": ref.model_dump(mode="json", exclude_none=True),
            "source": source.model_dump(mode="json", exclude_none=True),
        }

    def ids_payload(ids: PaperIds) -> dict[str, Any]:
        return ids.model_dump(mode="json", exclude_none=True)

    env.filters.update(topic_label=topic_label, long_date=long_date, authors=authors)
    env.globals.update(
        card_for=card_for,
        card_payload=card_payload,
        ids_payload=ids_payload,
        column_name=board.column_name,
        Column=board.Column,
        repo=site.repo,
        built_at=site.built_at,
        topics=site.topics,
    )
    return env


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(site: Site, out: Path) -> list[Path]:
    """Write every page under `out`, replacing what was there. Returns the pages."""
    if out.exists():
        shutil.rmtree(out)
    env = _environment(site)
    days = site.data.days
    pages: list[Path] = []

    def page(relative: str, template: str, **context: Any) -> None:
        depth = relative.count("/")
        root = "../" * depth
        path = out / relative
        _write(path, env.get_template(template).render(root=root, **context))
        pages.append(path)

    latest: Day | None = days[0] if days else None
    page(
        "index.html",
        "day.html",
        day=latest,
        newer=None,
        older=days[1] if len(days) > 1 else None,
        is_today=True,
        nav="today",
    )
    for i, day in enumerate(days):
        page(
            f"days/{day.date.isoformat()}/index.html",
            "day.html",
            day=day,
            newer=days[i - 1] if i > 0 else None,
            older=days[i + 1] if i + 1 < len(days) else None,
            is_today=False,
            nav="archive",
        )
    for day in days:
        if day.has_graphs:
            page(
                f"days/{day.date.isoformat()}/graph/index.html",
                "graph.html",
                day=day,
                layouts=[(v, graph_view.layout(v.graph)) for v in day.picks if v.graph],
                role_names=graph_view.ROLE_NAMES,
                nav="archive",
            )
    page("archive/index.html", "archive.html", days=days, nav="archive")
    page("settings/index.html", "settings.html", nav="settings")

    b = site.data.board
    done = sorted(
        b.column(board.Column.DONE),
        key=lambda c: (c.finished_on or c.added_on, c.paper.id),
        reverse=True,
    )
    page(
        "board/index.html",
        "board.html",
        to_read=b.column(board.Column.TO_READ),
        reading=b.column(board.Column.READING),
        done_recent=done[:DONE_SHOWN],
        done_older=done[DONE_SHOWN:],
        limit=board.TO_READ_LIMIT,
        nav="board",
    )
    shutil.copytree(STATIC, out / "static")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return pages
