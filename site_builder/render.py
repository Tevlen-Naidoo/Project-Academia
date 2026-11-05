"""Render the site into a folder of static pages built from web components (Lit)."""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from fetcher.topics import TopicsConfig
from site_builder import views
from site_builder.load import Day, PickView, SiteData

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
ROOT_FILES = HERE / "root"
"""Files served from the site root: the service worker must sit there to cover every page."""
DEFAULT_REPO = "Tevlen-Naidoo/Project-Academia"


@dataclass(frozen=True, slots=True)
class Site:
    repo: str
    topics: TopicsConfig
    data: SiteData
    built_at: dt.datetime
    push_key: str = ""
    """VAPID public key for Web Push; empty leaves notifications off."""
    firebase_config: str = ""
    """The Firebase web app config as JSON; empty leaves sign-in and the live board off."""


def _environment(site: Site) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    b = site.data.board

    def pick_data(view: PickView, day: Day, root: str) -> dict[str, Any]:
        return views.pick(view, day, root=root, topics=site.topics, b=b)

    def archive_data(days: list[Day], root: str) -> dict[str, Any]:
        return views.archive(days, root=root, topics=site.topics)

    env.filters.update(long_date=views.long_date)
    env.globals.update(
        pick_data=pick_data,
        archive_data=archive_data,
        board_data=lambda: views.board_data(b),
        graph_data=views.graph,
        repo=site.repo,
        built_at=site.built_at,
        push_key=site.push_key,
        firebase_config=site.firebase_config,
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
                graphs=[v for v in day.picks if v.graph],
                nav="archive",
            )
    page("archive/index.html", "archive.html", days=days, nav="archive")
    page("settings/index.html", "settings.html", nav="settings")

    page("board/index.html", "board.html", nav="board")
    shutil.copytree(STATIC, out / "static")
    for path in ROOT_FILES.iterdir():
        shutil.copy(path, out / path.name)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return pages
