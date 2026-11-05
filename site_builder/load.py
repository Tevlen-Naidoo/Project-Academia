"""Load everything the site shows, with the project's own models.

A data file that doesn't validate stops the build, so the site never shows a
half-broken page. A missing prerequisites file, graphs file or board is fine.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from reader import board
from reader.graph import GraphFile, PickGraph
from reader.models import PrereqLabel, PrereqsFile, Prerequisite
from scorer.models import Pick, PicksFile

PREREQ_ORDER = {PrereqLabel.READ_FIRST: 0, PrereqLabel.FOUNDATION: 1, PrereqLabel.BACKGROUND: 2}


@dataclass(frozen=True, slots=True)
class PickView:
    pick: Pick
    prerequisites: tuple[Prerequisite, ...] = ()
    graph: PickGraph | None = None


@dataclass(frozen=True, slots=True)
class Day:
    date: dt.date
    light_day: bool
    picks: tuple[PickView, ...]

    @property
    def has_graphs(self) -> bool:
        return any(p.graph is not None for p in self.picks)

    @property
    def topics(self) -> list[str]:
        return sorted({p.pick.topic for p in self.picks if p.pick.topic})

    @property
    def funnels(self) -> list[str]:
        return sorted({p.pick.funnel.value for p in self.picks})


@dataclass(slots=True)
class SiteData:
    days: list[Day] = field(default_factory=list)
    """Newest first."""
    board: board.Board = field(default_factory=board.Board)


def _prereqs(data_dir: Path, day: dt.date) -> dict[str, tuple[Prerequisite, ...]]:
    path = data_dir / "prereqs" / f"{day.isoformat()}.json"
    if not path.exists():
        return {}
    prereqs = PrereqsFile.model_validate_json(path.read_bytes())
    return {
        p.pick_id: tuple(sorted(p.prerequisites, key=lambda x: PREREQ_ORDER[x.label]))
        for p in prereqs.picks
    }


def _graphs(data_dir: Path, day: dt.date) -> dict[str, PickGraph]:
    path = data_dir / "graphs" / f"{day.isoformat()}.json"
    if not path.exists():
        return {}
    graphs = GraphFile.model_validate_json(path.read_bytes())
    return {g.pick_id: g for g in graphs.graphs if len(g.nodes) > 1}


def load(data_dir: Path) -> SiteData:
    days: list[Day] = []
    for path in sorted((data_dir / "picks").glob("*.json"), reverse=True):
        picks = PicksFile.model_validate_json(path.read_bytes())
        if not picks.picks:
            continue
        prereqs = _prereqs(data_dir, picks.date)
        graphs = _graphs(data_dir, picks.date)
        days.append(
            Day(
                date=picks.date,
                light_day=picks.light_day,
                picks=tuple(
                    PickView(
                        pick=p,
                        prerequisites=prereqs.get(p.paper.id, ()),
                        graph=graphs.get(p.paper.id),
                    )
                    for p in picks.picks
                ),
            )
        )
    return SiteData(days=days, board=board.load(data_dir / "board.json"))
