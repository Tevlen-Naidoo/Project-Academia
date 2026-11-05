"""The one-line reason shown under each pick."""

from __future__ import annotations

import datetime as dt

from fetcher.models import Paper, PublicationStatus
from scorer.models import Signal, SignalScore

SEPARATOR = " · "


def _phrase(signal: Signal, paper: Paper, today: dt.date) -> str | None:
    pub, cites, buzz = paper.publication, paper.citations, paper.buzz
    match signal:
        case Signal.STATUS:
            if pub.status is PublicationStatus.PREPRINT:
                return None
            verb = "Published in" if pub.status is PublicationStatus.PUBLISHED else "Accepted at"
            return f"{verb} {pub.venue}" if pub.venue else verb.split()[0]
        case Signal.BUZZ:
            parts = []
            if buzz.hf_upvotes:
                parts.append(f"{buzz.hf_upvotes} upvotes on Hugging Face")
            if buzz.hn_points:
                parts.append(f"{buzz.hn_points} points on Hacker News")
            return SEPARATOR.join(parts) or None
        case Signal.CITATION_SPEED:
            if not cites.total:
                return None
            noun = "citation" if cites.total == 1 else "citations"
            text = f"{cites.total} {noun}"
            if cites.per_month and cites.per_month >= 1:
                text += f", about {round(cites.per_month)} a month"
            return text
        case Signal.INFLUENTIAL:
            if not cites.influential:
                return None
            return f"{cites.influential} influential citations"
        case Signal.RECENCY:
            start = paper.dates.submitted
            if start is None:
                return None
            days = (today - start).days
            if days <= 0:
                return "New today"
            return "New this week" if days < 7 else None


def reason(paper: Paper, breakdown: tuple[SignalScore, ...], today: dt.date) -> str:
    """The two signals that added most to the score, in words."""
    ranked = sorted(
        (s for s in breakdown if s.known and s.weight > 0 and s.value > 0),
        key=lambda s: s.value * s.weight,
        reverse=True,
    )
    phrases: list[str] = []
    for s in ranked:
        phrase = _phrase(s.signal, paper, today)
        if phrase:
            phrases.append(phrase)
        if len(phrases) == 2:
            break
    return SEPARATOR.join(phrases) or "Top-scoring paper in its topic today"
