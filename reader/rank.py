"""Rank a pick's references and label the prerequisites.

Signals, strongest first: S2 marks the reference influential for the pick; the
pick cites it for its method; it is well established (citations, log scale); it
is shared with other picks; it is a survey, which makes a good entry point.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from reader.models import PrereqLabel, Prerequisite
from reader.references import Reference

READ_FIRST_MAX = 2
BACKGROUND_MAX = 3
REVIEWS_IN_BACKGROUND_MAX = 1
READ_FIRST_MIN_CITATIONS = 100
UNPROCESSED_READ_FIRST_MIN_CITATIONS = 1000
"""When S2 has not yet marked any reference influential (common for a week-old
paper), a methodology reference needs this many citations to count as Read first."""
BACKGROUND_MIN_CITATIONS = 10
QUOTE_MAX_CHARS = 300

W_INFLUENTIAL = 3.0
W_METHODOLOGY = 2.0
W_RESULT = 0.5
W_CITATIONS = 0.8
W_INFLUENTIAL_CITATIONS = 0.5
W_SHARED = 1.5
W_REVIEW = 0.5


def has_influence_signal(refs: Iterable[Reference]) -> bool:
    """True when S2 has processed the list: at least one reference is marked influential."""
    return any(r.influential for r in refs)


def score(ref: Reference, shared: int = 1) -> float:
    intents = ref.intents or []
    p = ref.paper
    return round(
        W_INFLUENTIAL * bool(ref.influential)
        + W_METHODOLOGY * ("methodology" in intents)
        + W_RESULT * ("result" in intents)
        + W_CITATIONS * math.log10(1 + (p.citations or 0))
        + W_INFLUENTIAL_CITATIONS * math.log10(1 + (p.influential_citations or 0))
        + W_SHARED * max(shared - 1, 0)
        + W_REVIEW * p.is_review,
        3,
    )


def is_read_first(ref: Reference, *, processed: bool) -> bool:
    """Whether the citing paper builds directly on this reference.

    OpenAlex references (no influence flag or intents) are never Read first.
    """
    if ref.influential is None or ref.intents is None:
        return False
    citations = ref.paper.citations or 0
    if "methodology" not in ref.intents:
        return False
    if processed:
        return ref.influential and citations >= READ_FIRST_MIN_CITATIONS
    return citations >= UNPROCESSED_READ_FIRST_MIN_CITATIONS


def _count(n: int) -> str:
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def reasons(ref: Reference, shared: int = 1) -> list[str]:
    out: list[str] = []
    intents = ref.intents or []
    if ref.influential:
        out.append("Influential reference for this paper")
    if "methodology" in intents:
        out.append("Cited for its method")
    elif "result" in intents:
        out.append("Cited for its results")
    if ref.paper.citations is not None:
        out.append(f"{_count(ref.paper.citations)} citations")
    if shared > 1:
        others = shared - 1
        out.append(f"Also a reference of {others} other pick{'s' if others > 1 else ''}")
    if ref.paper.is_review:
        out.append("Survey: a good entry point")
    return out


def quote(ref: Reference) -> str | None:
    for context in ref.contexts:
        text = " ".join(context.split())
        if text:
            return text if len(text) <= QUOTE_MAX_CHARS else text[: QUOTE_MAX_CHARS - 1] + "…"
    return None


def on_topic(ref: Reference, pick_fields: frozenset[str]) -> bool:
    """Whether a reference can be Background for a pick in `pick_fields`.

    Yes when S2 says the pick builds on it (influential, or cited for its method
    or results), or when it shares a field of study with the pick. A reference
    with no known field is left out once the pick's fields are known.
    """
    if not pick_fields:
        return True
    intents = ref.intents or []
    if ref.influential or "methodology" in intents or "result" in intents:
        return True
    return bool(ref.fields & pick_fields)


def unique(refs: Iterable[Reference]) -> list[Reference]:
    """One entry per paper; a duplicate keeps the strongest signals."""
    best: dict[str, Reference] = {}
    for ref in refs:
        seen = best.get(ref.paper.id)
        if seen is None or score(ref) > score(seen):
            best[ref.paper.id] = ref
    return list(best.values())


def select(
    pick_id: str,
    refs: Iterable[Reference],
    *,
    shared: Mapping[str, int] | None = None,
    exclude: frozenset[str] | set[str] = frozenset(),
    pick_fields: frozenset[str] = frozenset(),
) -> tuple[list[Prerequisite], list[Reference]]:
    """Up to 2 Read first and 3 Background prerequisites for one pick.

    Background papers must be on topic for the pick (`on_topic`), so a highly
    cited paper from another field is not suggested on citation count alone.

    Returns the prerequisites and the Read first references (for the Foundation step).
    """
    shared = shared or {}
    pool = [r for r in unique(refs) if r.paper.id not in exclude and r.paper.id != pick_id]
    processed = has_influence_signal(pool)
    ranked = sorted(pool, key=lambda r: score(r, shared.get(r.paper.id, 1)), reverse=True)

    read_first = [r for r in ranked if is_read_first(r, processed=processed)][:READ_FIRST_MAX]
    chosen = {r.paper.id for r in read_first}
    background: list[Reference] = []
    reviews = 0
    for ref in ranked:
        if len(background) >= BACKGROUND_MAX:
            break
        if ref.paper.id in chosen or (ref.paper.citations or 0) < BACKGROUND_MIN_CITATIONS:
            continue
        if not on_topic(ref, pick_fields):
            continue
        if ref.paper.is_review:
            if reviews >= REVIEWS_IN_BACKGROUND_MAX:
                continue
            reviews += 1
        background.append(ref)

    def make(ref: Reference, label: PrereqLabel) -> Prerequisite:
        n = shared.get(ref.paper.id, 1)
        return Prerequisite(
            paper=ref.paper.model_copy(deep=True),
            label=label,
            score=score(ref, n),
            reasons=reasons(ref, n),
            quote=quote(ref),
            supports=[pick_id],
        )

    prereqs = [make(r, PrereqLabel.READ_FIRST) for r in read_first]
    prereqs += [make(r, PrereqLabel.BACKGROUND) for r in background]
    return prereqs, read_first


def best_read_first(refs: Iterable[Reference], *, skip: set[str]) -> Reference | None:
    """The top Read first reference of a paper, for one hop of the Foundation chain."""
    pool = [r for r in unique(refs) if r.paper.id not in skip]
    processed = has_influence_signal(pool)
    candidates = [r for r in pool if is_read_first(r, processed=processed)]
    return max(candidates, key=score, default=None)
