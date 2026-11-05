"""Turn a day's candidates into scores from 0 to 1.

Each signal becomes a percentile among the day's candidates, so buzz,
citations and status can be weighted against each other. Citation signals are
ranked within the paper's main topic, so slow-citing fields can compete. Each
funnel's scores are then scaled so its best paper scores 1, so new papers and
making-waves papers compete on equal terms.
"""

from __future__ import annotations

import bisect
import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from fetcher.enrich import buzz_score
from fetcher.models import Funnel, Paper
from scorer.config import ScoringConfig, Weights
from scorer.models import Signal, SignalScore

MEDIAN = 0.5
"""Used when a source had no data, so a paper isn't punished for an outage."""


def main_topic(paper: Paper) -> str | None:
    return paper.topics[0] if paper.topics else None


class Percentiles:
    """Mid-rank percentiles of non-negative values; 0 always maps to 0."""

    def __init__(self, values: Sequence[float]) -> None:
        self._sorted = sorted(values)

    def __call__(self, value: float) -> float:
        if value <= 0 or not self._sorted:
            return 0.0
        below = bisect.bisect_left(self._sorted, value)
        equal = bisect.bisect_right(self._sorted, value) - below
        return (below + 0.5 * equal) / len(self._sorted)


@dataclass(frozen=True, slots=True)
class Scored:
    paper: Paper
    funnel: Funnel
    score: float
    breakdown: tuple[SignalScore, ...]

    @property
    def topic(self) -> str | None:
        return main_topic(self.paper)


def _recency(paper: Paper, today: dt.date, days: int) -> float | None:
    start = paper.dates.submitted or paper.dates.published
    if start is None:
        return None
    age = (today - start).days
    return min(max(1 - age / days, 0.0), 1.0)


def score_all(papers: Sequence[Paper], config: ScoringConfig, today: dt.date) -> list[Scored]:
    """Every paper with its best funnel score and the breakdown behind it."""
    buzz = Percentiles([buzz_score(p) for p in papers])
    speed: dict[str | None, list[float]] = defaultdict(list)
    influential: dict[str | None, list[float]] = defaultdict(list)
    for p in papers:
        if p.citations.per_month is not None:
            speed[main_topic(p)].append(p.citations.per_month)
        if p.citations.influential is not None:
            influential[main_topic(p)].append(float(p.citations.influential))
    speed_pct = {t: Percentiles(v) for t, v in speed.items()}
    influential_pct = {t: Percentiles(v) for t, v in influential.items()}

    raw_scores: list[list[Scored]] = []
    for p in papers:
        topic = main_topic(p)
        raw: dict[Signal, float | None] = {
            Signal.BUZZ: buzz(buzz_score(p)),
            Signal.STATUS: config.status_values[p.publication.status],
            Signal.CITATION_SPEED: (
                speed_pct[topic](p.citations.per_month)
                if p.citations.per_month is not None
                else None
            ),
            Signal.INFLUENTIAL: (
                influential_pct[topic](float(p.citations.influential))
                if p.citations.influential is not None
                else None
            ),
            Signal.RECENCY: _recency(p, today, config.recency_days),
        }
        raw_scores.append([_weigh(p, f, raw, config.weights[f]) for f in p.funnel])

    # Each funnel's weights reach different heights (a new preprint has no citations
    # yet), so scale each funnel's scores until its best paper scores 1.
    top: dict[Funnel, float] = {}
    for options in raw_scores:
        for s in options:
            top[s.funnel] = max(top.get(s.funnel, 0.0), s.score)
    out: list[Scored] = []
    for options in raw_scores:
        scaled = [
            replace(s, score=round(s.score / top[s.funnel], 4) if top[s.funnel] else 0.0)
            for s in options
        ]
        out.append(max(scaled, key=lambda s: s.score))
    return out


def _weigh(
    paper: Paper, funnel: Funnel, raw: dict[Signal, float | None], weights: Weights
) -> Scored:
    parts = tuple(
        SignalScore(
            signal=signal,
            value=round(MEDIAN if value is None else value, 4),
            weight=getattr(weights, signal.value),
            known=value is not None,
        )
        for signal, value in raw.items()
    )
    total = sum(s.value * s.weight for s in parts)
    return Scored(paper=paper, funnel=funnel, score=min(total, 1.0), breakdown=parts)
