"""Citation graphs around each pick: what it builds on and what built on it.

Saved as data/graphs/YYYY-MM-DD.json and drawn by the site's graph page. Each
graph holds the pick, its prerequisites, its other top references, and the
papers that cite it most, with "A cites B" edges between any two of them.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt

from fetcher.models import Paper
from reader import rank
from reader.lookup import ref_from_paper
from reader.models import PaperId, PaperRef, PrereqLabel, PrereqsFile
from reader.references import Reference, ReferenceClient, s2_key

SCHEMA_VERSION: Literal[1] = 1
OTHER_REFERENCES_MAX = 15
CITING_MAX = 10


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class NodeRole(StrEnum):
    PICK = "pick"
    READ_FIRST = "read_first"
    FOUNDATION = "foundation"
    BACKGROUND = "background"
    REFERENCE = "reference"
    """Another reference of the pick."""
    CITING = "citing"
    """A later paper that cites the pick."""


class EdgeKind(StrEnum):
    CITES = "cites"
    BUILDS_ON = "builds_on"
    """A Read first paper to its Foundation, through the papers in between."""


class GraphNode(_Model):
    paper: PaperRef
    role: NodeRole


class GraphEdge(_Model):
    source: PaperId
    """The citing paper."""
    target: PaperId
    """The cited paper."""
    kind: EdgeKind = EdgeKind.CITES


class PickGraph(_Model):
    pick_id: PaperId
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    citing_seen: NonNegativeInt = 0
    errors: list[str] = Field(default_factory=list)


class GraphFile(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    date: dt.date
    generated_at: AwareDatetime
    graphs: list[PickGraph] = Field(default_factory=list)


ROLE_OF_LABEL = {
    PrereqLabel.READ_FIRST: NodeRole.READ_FIRST,
    PrereqLabel.FOUNDATION: NodeRole.FOUNDATION,
    PrereqLabel.BACKGROUND: NodeRole.BACKGROUND,
}


def pick_ref(pick: Paper) -> PaperRef:
    return ref_from_paper(pick)


def build_one(
    pick: Paper,
    refs: Sequence[Reference],
    prereqs: PrereqsFile,
    client: ReferenceClient | None,
    *,
    pick_fields: frozenset[str] = frozenset(),
) -> PickGraph:
    graph = PickGraph(pick_id=pick.id)
    nodes: dict[str, GraphNode] = {pick.id: GraphNode(paper=pick_ref(pick), role=NodeRole.PICK)}

    entry = next((p for p in prereqs.picks if p.pick_id == pick.id), None)
    chains: list[list[str]] = []
    for pre in entry.prerequisites if entry else ():
        nodes.setdefault(pre.paper.id, GraphNode(paper=pre.paper, role=ROLE_OF_LABEL[pre.label]))
        if pre.chain:
            chains.append(pre.chain)

    others = sorted(
        (r for r in rank.unique(refs) if r.paper.id not in nodes and rank.on_topic(r, pick_fields)),
        key=rank.score,
        reverse=True,
    )
    for ref in others[:OTHER_REFERENCES_MAX]:
        nodes[ref.paper.id] = GraphNode(paper=ref.paper, role=NodeRole.REFERENCE)

    edges: dict[tuple[str, str], GraphEdge] = {}

    def add(source: str, target: str, kind: EdgeKind = EdgeKind.CITES) -> None:
        if source != target and source in nodes and target in nodes:
            edges.setdefault((source, target), GraphEdge(source=source, target=target, kind=kind))

    ref_ids = {r.paper.id for r in refs}
    for node_id in nodes:
        if node_id in ref_ids:
            add(pick.id, node_id)
    for chain in chains:
        add(chain[0], chain[-1], EdgeKind.BUILDS_ON)

    if client is not None:
        key = s2_key(pick.ids)
        if key:
            citing = client.s2_citations(key)
            graph.citing_seen = len(citing.references)
            graph.errors.extend(citing.errors)
            top = sorted(
                (c for c in citing.references if c.paper.id not in nodes),
                key=lambda c: c.paper.citations or 0,
                reverse=True,
            )
            for ref in top[:CITING_MAX]:
                nodes[ref.paper.id] = GraphNode(paper=ref.paper, role=NodeRole.CITING)
                add(ref.paper.id, pick.id)
        links, errors = client.s2_reference_ids([n.paper.ids.s2 for n in nodes.values()])
        graph.errors.extend(errors)
        _cross_link(nodes, links, add)

    graph.nodes = list(nodes.values())
    graph.edges = list(edges.values())
    return graph


def _cross_link(
    nodes: Mapping[str, GraphNode],
    links: Mapping[str, Iterable[str]],
    add: Callable[[str, str], None],
) -> None:
    by_s2 = {n.paper.ids.s2: node_id for node_id, n in nodes.items() if n.paper.ids.s2}
    for source_s2, targets in links.items():
        source = by_s2.get(source_s2)
        if source is None:
            continue
        for target_s2 in targets:
            target = by_s2.get(target_s2)
            if target is not None:
                add(source, target)


def build(
    picks: Sequence[Paper],
    fetched: Mapping[str, Sequence[Reference]],
    prereqs: PrereqsFile,
    client: ReferenceClient | None,
    *,
    fields: Mapping[str, frozenset[str]] | None = None,
    now: dt.datetime | None = None,
) -> GraphFile:
    out = GraphFile(date=prereqs.date, generated_at=now or dt.datetime.now(dt.UTC))
    for pick in picks:
        out.graphs.append(
            build_one(
                pick,
                fetched.get(pick.id, ()),
                prereqs,
                client,
                pick_fields=(fields or {}).get(pick.id, frozenset()),
            )
        )
    return out
