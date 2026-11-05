"""Lay out a pick's citation graph as an SVG drawing: time runs left to right.

x is the publication year; papers that cite the pick get their own column on the
right. Each prerequisite gets a row of its own at the top so its label never
collides with anything, the pick sits on the row below them, and the other
references stack under it by year. The drawing is the same on every build.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from reader.graph import EdgeKind, GraphEdge, GraphNode, NodeRole, PickGraph

WIDTH = 960
MARGIN_X = 48
CITING_GAP = 110
"""Room between the newest year and the "Cited by" column."""
MARGIN_TOP = 40
ROW = 34
LABEL_CHARS = 38
ROLE_ORDER = {
    NodeRole.PICK: 0,
    NodeRole.READ_FIRST: 1,
    NodeRole.FOUNDATION: 2,
    NodeRole.BACKGROUND: 3,
    NodeRole.REFERENCE: 4,
    NodeRole.CITING: 5,
}
LABELLED = frozenset({NodeRole.READ_FIRST, NodeRole.FOUNDATION, NodeRole.BACKGROUND})
ROLE_NAMES = {
    NodeRole.PICK: "This paper",
    NodeRole.READ_FIRST: "Read first",
    NodeRole.FOUNDATION: "Foundation",
    NodeRole.BACKGROUND: "Background",
    NodeRole.REFERENCE: "Other references",
    NodeRole.CITING: "Cited by",
}


@dataclass(frozen=True, slots=True)
class PlacedNode:
    node: GraphNode
    x: float
    y: float
    r: float
    label: str | None
    label_left: bool


@dataclass(frozen=True, slots=True)
class PlacedEdge:
    edge: GraphEdge
    x1: float
    y1: float
    x2: float
    y2: float
    touches_pick: bool


@dataclass(frozen=True, slots=True)
class Ticks:
    label: str
    x: float


@dataclass(frozen=True, slots=True)
class Layout:
    graph: PickGraph
    width: int
    height: int
    nodes: list[PlacedNode]
    edges: list[PlacedEdge]
    ticks: list[Ticks]
    groups: list[tuple[str, list[GraphNode]]]
    """Nodes by role, for the list under the drawing."""


def _radius(node: GraphNode) -> float:
    if node.role is NodeRole.PICK:
        return 12.0
    return min(4.0 + 1.6 * math.log10(1 + (node.paper.citations or 0)), 11.0)


def _short(title: str) -> str:
    return title if len(title) <= LABEL_CHARS else title[: LABEL_CHARS - 1].rstrip() + "…"


def layout(graph: PickGraph) -> Layout:
    pick = next((n for n in graph.nodes if n.role is NodeRole.PICK), None)
    fallback_year = pick.paper.year if pick and pick.paper.year else 2000
    citing = [n for n in graph.nodes if n.role is NodeRole.CITING]
    dated = [n for n in graph.nodes if n.role is not NodeRole.CITING]
    years = [n.paper.year or fallback_year for n in dated]
    lo, hi = (min(years), max(years)) if years else (fallback_year, fallback_year)
    span = max(hi - lo, 1)
    right = WIDTH - MARGIN_X - (CITING_GAP if citing else 0)
    citing_x = WIDTH - MARGIN_X

    def x_of(year: int) -> float:
        if hi == lo:
            return (MARGIN_X + right) / 2
        return MARGIN_X + (year - lo) / span * (right - MARGIN_X)

    def year_x(node: GraphNode) -> float:
        return round(x_of(node.paper.year or fallback_year), 1)

    placed: dict[str, PlacedNode] = {}

    def put(node: GraphNode, x: float, y: float) -> None:
        placed[node.paper.id] = PlacedNode(
            node=node,
            x=x,
            y=round(y, 1),
            r=round(_radius(node), 1),
            label=_short(node.paper.title) if node.role in LABELLED else None,
            label_left=x > WIDTH * 0.62,
        )

    y = MARGIN_TOP + ROW / 2
    labelled = sorted(
        (n for n in dated if n.role in LABELLED),
        key=lambda n: (ROLE_ORDER[n.role], n.paper.year or fallback_year, n.paper.id),
    )
    for node in labelled:
        put(node, year_x(node), y)
        y += ROW
    pick_y = y
    if pick is not None:
        put(pick, year_x(pick), pick_y)

    stacks: dict[float, int] = {}
    others = sorted(
        (n for n in dated if n.role is NodeRole.REFERENCE),
        key=lambda n: (-(n.paper.citations or 0), n.paper.id),
    )
    for node in others:
        x = year_x(node)
        depth = stacks.get(x, 0) + 1
        stacks[x] = depth
        put(node, x, pick_y + depth * ROW)

    citing_sorted = sorted(citing, key=lambda n: (-(n.paper.citations or 0), n.paper.id))
    first = max(pick_y - (len(citing_sorted) - 1) / 2 * ROW, MARGIN_TOP + ROW / 2)
    for i, node in enumerate(citing_sorted):
        put(node, citing_x, first + i * ROW)

    bottom = max((p.y for p in placed.values()), default=pick_y)
    height = bottom + ROW

    pick_id = graph.pick_id
    edges = [
        PlacedEdge(
            edge=e,
            x1=placed[e.source].x,
            y1=placed[e.source].y,
            x2=placed[e.target].x,
            y2=placed[e.target].y,
            touches_pick=pick_id in (e.source, e.target) or e.kind is EdgeKind.BUILDS_ON,
        )
        for e in graph.edges
        if e.source in placed and e.target in placed
    ]
    # Quiet edges first, so the ones touching the pick draw on top.
    edges.sort(key=lambda e: e.touches_pick)

    step = 1 if span <= 12 else 5 if span <= 60 else 10
    first = lo if step == 1 else (lo // step + 1) * step
    ticks = [Ticks(label=str(y), x=round(x_of(y), 1)) for y in range(first, hi + 1, step)]
    if hi == lo:
        ticks = [Ticks(label=str(lo), x=round(x_of(lo), 1))]
    if citing:
        ticks.append(Ticks(label="Cited by", x=citing_x))

    groups = [
        (
            ROLE_NAMES[role],
            sorted(
                (n for n in graph.nodes if n.role is role),
                key=lambda n: -(n.paper.citations or 0),
            ),
        )
        for role in ROLE_ORDER
    ]
    return Layout(
        graph=graph,
        width=WIDTH,
        height=int(height),
        nodes=sorted(placed.values(), key=lambda p: -ROLE_ORDER[p.node.role]),
        edges=edges,
        ticks=ticks,
        groups=[(name, nodes) for name, nodes in groups if nodes],
    )
