"""Choose the day's picks from the scored candidates.

Rules, in order: skip papers on the reading board and recent picks; on light
days hold one slot for a light topic; spread topics across days; never use one
main topic twice in a day unless nothing else is left.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from fetcher.models import PaperIds
from fetcher.topics import TopicsConfig
from scorer.config import WEEKDAYS, ScoringConfig
from scorer.models import Pick, PicksFile, Slot
from scorer.reasons import reason
from scorer.signals import Scored

PICKS_SUBDIR = "picks"


def id_forms(ids: PaperIds) -> set[str]:
    """Every id a paper can be known by, in canonical-id form."""
    out: set[str] = set()
    if ids.arxiv:
        out.add(f"arxiv:{ids.arxiv}")
    if ids.doi:
        out.add(f"doi:{ids.doi.lower()}")
    if ids.openalex:
        out.add(f"openalex:{ids.openalex}")
    if ids.s2:
        out.add(f"s2:{ids.s2}")
    return out


@dataclass(slots=True)
class History:
    """What earlier picks files say, for days before today."""

    last_picked: dict[str, dt.date] = field(default_factory=dict)
    """Every id form of every past pick, with the last day it was picked."""
    topic_days: dict[str, set[dt.date]] = field(default_factory=dict)
    """Each main topic, with the days a pick had it."""

    def add(self, picks: PicksFile) -> None:
        for pick in picks.picks:
            for form in id_forms(pick.paper.ids):
                self.last_picked[form] = max(self.last_picked.get(form, picks.date), picks.date)
            if pick.topic:
                self.topic_days.setdefault(pick.topic, set()).add(picks.date)

    def last_pick_of(self, ids: PaperIds) -> dt.date | None:
        dates = [self.last_picked[f] for f in id_forms(ids) if f in self.last_picked]
        return max(dates, default=None)

    def recent_topic_days(self, topic: str | None, today: dt.date, days: int) -> int:
        """On how many of the `days` days before `today` a pick had this main topic."""
        if topic is None:
            return 0
        return sum(1 for d in self.topic_days.get(topic, ()) if 0 < (today - d).days <= days)


def load_history(data_dir: Path, today: dt.date) -> tuple[History, list[str]]:
    """All picks files dated before `today`, and any that could not be read."""
    history = History()
    broken: list[str] = []
    for path in sorted((data_dir / PICKS_SUBDIR).glob("*.json")):
        try:
            day = dt.date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day >= today:
            continue
        try:
            history.add(PicksFile.model_validate_json(path.read_bytes()))
        except (OSError, ValidationError):
            broken.append(path.name)  # an old broken file should not stop today's picks
    return history, broken


def is_light_day(today: dt.date, config: ScoringConfig) -> bool:
    return WEEKDAYS[today.weekday()] in config.light_days


@dataclass(frozen=True, slots=True)
class _Candidate:
    scored: Scored
    adjusted: float
    repick: bool
    light: bool


def _candidates(
    scored: Iterable[Scored],
    *,
    config: ScoringConfig,
    topics: TopicsConfig,
    today: dt.date,
    history: History,
    exclude: AbstractSet[str],
) -> list[_Candidate]:
    light_topics = {tag for tag, t in topics.topics.items() if t.weight == "light"}
    out: list[_Candidate] = []
    for s in scored:
        if (id_forms(s.paper.ids) | {s.paper.id}) & exclude:
            continue
        last = history.last_pick_of(s.paper.ids)
        if last is not None and (today - last).days <= config.repeats.cooldown_days:
            continue
        factor = config.repeats.factor if last is not None else 1.0
        recent = history.recent_topic_days(s.topic, today, config.topic_spread.days)
        factor *= config.topic_spread.factor**recent
        out.append(
            _Candidate(
                scored=s,
                adjusted=round(s.score * factor, 4),
                repick=last is not None,
                light=s.topic in light_topics,
            )
        )
    out.sort(key=lambda c: (-c.adjusted, c.scored.paper.id))
    return out


def choose(
    scored: Sequence[Scored],
    *,
    config: ScoringConfig,
    topics: TopicsConfig,
    today: dt.date,
    history: History,
    exclude: AbstractSet[str] = frozenset(),
) -> tuple[list[Pick], list[str], bool]:
    """The picks in rank order, notes on any fallback used, and whether it is a light day."""
    pool = _candidates(
        scored, config=config, topics=topics, today=today, history=history, exclude=exclude
    )
    notes: list[str] = []
    light_day = is_light_day(today, config)
    chosen: list[tuple[_Candidate, Slot]] = []
    used_topics: set[str | None] = set()

    def take(candidate: _Candidate, slot: Slot) -> None:
        chosen.append((candidate, slot))
        used_topics.add(candidate.scored.topic)
        pool.remove(candidate)

    if light_day:
        light = next((c for c in pool if c.light), None)
        if light is not None:
            take(light, Slot.LIGHT)
        else:
            notes.append("Light day, but no light-topic candidates: all slots were open.")

    passes: tuple[tuple[str, Callable[[_Candidate], bool]], ...] = (
        ("core, new topic", lambda c: not c.light and c.scored.topic not in used_topics),
        ("light, new topic", lambda c: c.scored.topic not in used_topics),
        ("any topic", lambda c: True),
    )
    for name, allowed in passes:
        while len(chosen) < config.picks_per_day:
            candidate = next((c for c in pool if allowed(c)), None)
            if candidate is None:
                break
            if name != passes[0][0]:
                notes.append(f"Fallback ({name}): {candidate.scored.paper.id}")
            take(candidate, Slot.OPEN)

    if len(chosen) < config.picks_per_day:
        notes.append(f"Only {len(chosen)} of {config.picks_per_day} picks: no candidates left.")

    chosen.sort(key=lambda cs: (-cs[0].adjusted, cs[0].scored.paper.id))
    picks = [
        Pick(
            rank=rank,
            slot=slot,
            topic=c.scored.topic,
            funnel=c.scored.funnel,
            score=c.scored.score,
            adjusted=c.adjusted,
            breakdown=list(c.scored.breakdown),
            reason=reason(c.scored.paper, c.scored.breakdown, today),
            repick=c.repick,
            paper=c.scored.paper,
        )
        for rank, (c, slot) in enumerate(chosen, start=1)
    ]
    return picks, notes, light_day
