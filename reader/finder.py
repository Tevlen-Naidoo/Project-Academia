"""Find prerequisite reading for the day's picks.

For each pick: fetch its references (S2, else OpenAlex), rank them, keep up to
2 Read first and 3 Background papers, then follow each Read first paper's own
Read first chain down (at most 3 hops) and show the lowest paper as its
Foundation. Papers shown in the last 30 days are skipped.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from fetcher.http import HttpClient
from fetcher.models import Paper
from fetcher.sources.semantic_scholar import SemanticScholarSource
from fetcher.topics import TopicsConfig
from reader import graph, rank
from reader.fields import pick_fields
from reader.models import (
    PaperRef,
    PickPrereqs,
    PrereqLabel,
    PrereqsFile,
    Prerequisite,
    ReferenceSource,
)
from reader.references import Reference, ReferenceClient, s2_key

DATA_DIR = Path("data")
PREREQS_SUBDIR = "prereqs"
GRAPHS_SUBDIR = "graphs"
HISTORY_DAYS = 30
FOUNDATION_MAX_HOPS = 3


@dataclass(slots=True)
class FinderOptions:
    today: dt.date
    foundation_hops: int = FOUNDATION_MAX_HOPS
    exclude: set[str] = field(default_factory=set)
    """Paper ids never to show: past picks and prerequisites, papers already read."""
    topics: TopicsConfig | None = None
    """When set, Background papers must share a field of study with their pick."""


def history(data_dir: Path, today: dt.date, days: int = HISTORY_DAYS) -> set[str]:
    """Ids shown in prerequisite files from the `days` before `today`."""
    out: set[str] = set()
    folder = data_dir / PREREQS_SUBDIR
    for offset in range(1, days + 1):
        path = folder / f"{(today - dt.timedelta(days=offset)).isoformat()}.json"
        if not path.exists():
            continue
        try:
            out |= PrereqsFile.model_validate_json(path.read_bytes()).shown_ids()
        except (OSError, ValidationError):
            continue  # a broken old file should not stop today's run
    return out


def foundation_chain(
    client: ReferenceClient, start: PaperRef, *, skip: set[str], hops: int
) -> list[PaperRef]:
    """Follow Read first papers down from `start`; the last entry is the Foundation.

    Stops when no reference qualifies, after `hops` hops, or on reaching a paper in `skip`.
    """
    path: list[PaperRef] = []
    visited = {start.id}
    current = start
    for _ in range(hops):
        key = s2_key(current.ids)
        if key is None:
            break  # only S2 says what a paper builds on
        refs = client.s2_references(key)
        best = rank.best_read_first(refs.references, skip=visited)
        if best is None or best.paper.id in skip:
            break
        path.append(best.paper)
        visited.add(best.paper.id)
        current = best.paper
    return path


def _foundation(read_first: PaperRef, path: list[PaperRef], pick_id: str) -> Prerequisite:
    titles = [read_first.title, *(p.title for p in path)]
    because = "Builds on " + ", which builds on ".join(f"“{t}”" for t in titles[1:])
    paper = path[-1]
    ref = Reference(paper=paper)
    return Prerequisite(
        paper=paper.model_copy(deep=True),
        label=PrereqLabel.FOUNDATION,
        score=rank.score(ref),
        reasons=[f"Foundation under “{read_first.title}”", because, *rank.reasons(ref)],
        supports=[pick_id],
        chain=[read_first.id, *(p.id for p in path)],
    )


Fetched = dict[str, tuple[str, list[Reference], list[str]]]
"""Per pick id: where the references came from, the references, and errors."""


def fetch(picks: Sequence[Paper], client: ReferenceClient) -> Fetched:
    fetched: Fetched = {}
    for pick in picks:
        source, listing = client.references(pick.ids)
        fetched[pick.id] = (source, listing.references, listing.errors)
    return fetched


def fields_by_pick(picks: Sequence[Paper], options: FinderOptions) -> dict[str, frozenset[str]]:
    if options.topics is None:
        return {}
    return {p.id: pick_fields(p, options.topics) for p in picks}


def find(
    picks: Sequence[Paper],
    client: ReferenceClient,
    options: FinderOptions,
    *,
    s2: SemanticScholarSource | None = None,
    now: dt.datetime | None = None,
    fetched: Fetched | None = None,
) -> PrereqsFile:
    pick_ids = {p.id for p in picks}
    skip = options.exclude | pick_ids
    if fetched is None:
        fetched = fetch(picks, client)
    fields = fields_by_pick(picks, options)

    shared: Counter[str] = Counter()
    for _, refs, _ in fetched.values():
        shared.update({r.paper.id for r in refs})

    out = PrereqsFile(date=options.today, generated_at=now or dt.datetime.now(dt.UTC))
    for pick in picks:
        source, refs, errors = fetched[pick.id]
        prereqs, read_first = rank.select(
            pick.id, refs, shared=shared, exclude=skip, pick_fields=fields.get(pick.id, frozenset())
        )
        taken = {p.paper.id for p in prereqs}
        for ref in read_first:
            path = foundation_chain(
                client, ref.paper, skip=skip | taken, hops=options.foundation_hops
            )
            if path and path[-1].id not in taken:
                prereqs.append(_foundation(ref.paper, path, pick.id))
                taken.add(path[-1].id)
        out.picks.append(
            PickPrereqs(
                pick_id=pick.id,
                pick_title=pick.title,
                source=ReferenceSource(source),
                references_seen=len(refs),
                prerequisites=prereqs,
                errors=errors,
            )
        )
    _merge_supports(out)
    if s2 is not None:
        _add_tldrs(out, s2)
    return out


def _merge_supports(out: PrereqsFile) -> None:
    """A paper that is a prerequisite for several picks lists all of them."""
    supports: dict[str, list[str]] = {}
    for pick in out.picks:
        for pre in pick.prerequisites:
            supports.setdefault(pre.paper.id, []).append(pick.pick_id)
    for pick in out.picks:
        for pre in pick.prerequisites:
            pre.supports = list(dict.fromkeys(supports[pre.paper.id]))


def _add_tldrs(out: PrereqsFile, s2: SemanticScholarSource) -> None:
    refs = [pre.paper for pick in out.picks for pre in pick.prerequisites if pre.paper.ids.s2]
    keys = list(dict.fromkeys(p.ids.s2 for p in refs if p.ids.s2))
    if not keys:
        return
    result = s2.batch(keys)
    for paper in refs:
        found = result.papers.get(paper.ids.s2 or "")
        if found and found.tldr and found.tldr.text:
            paper.tldr = found.tldr.text
    if result.errors and out.picks:
        out.picks[0].errors.extend(f"TLDR lookup: {e}" for e in result.errors)


def run(
    http: HttpClient, picks: Sequence[Paper], options: FinderOptions
) -> tuple[PrereqsFile, graph.GraphFile]:
    client = ReferenceClient(http)
    fetched = fetch(picks, client)
    prereqs = find(picks, client, options, s2=SemanticScholarSource(http), fetched=fetched)
    refs = {pick_id: entry[1] for pick_id, entry in fetched.items()}
    graphs = graph.build(
        picks,
        refs,
        prereqs,
        client,
        fields=fields_by_pick(picks, options),
        now=prereqs.generated_at,
    )
    return prereqs, graphs


def write(prereqs: PrereqsFile, data_dir: Path = DATA_DIR) -> Path:
    folder = data_dir / PREREQS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{prereqs.date.isoformat()}.json"
    path.write_text(prereqs.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    return path


def write_graphs(graphs: graph.GraphFile, data_dir: Path = DATA_DIR) -> Path:
    folder = data_dir / GRAPHS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{graphs.date.isoformat()}.json"
    path.write_text(graphs.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    return path
