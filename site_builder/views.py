"""Turn the site's data into the plain JSON its web components draw.

Each page is a few custom elements (see static/js/components/) fed one of these
dicts as a JSON attribute. Links inside the site are worked out here, relative to
the page, so the components never guess paths.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fetcher.models import Links, Paper
from fetcher.topics import TopicsConfig
from reader import board
from reader.graph import EdgeKind
from reader.lookup import ref_from_paper
from reader.models import PaperRef
from site_builder import graph_view
from site_builder.load import Day, PickView


def paper_link(links: Links) -> str:
    """The best page to open a paper on."""
    for url in (links.abstract_page, links.doi, links.pdf):
        if url:
            return str(url)
    return ""


def long_date(day: dt.date) -> str:
    return f"{day:%A} {day.day} {day:%B %Y}"


def topic_label(topics: TopicsConfig, tag: str | None) -> str:
    if tag is None:
        return "Other"
    topic = topics.topics.get(tag)
    return topic.label if topic else tag


def authors(paper: Paper, limit: int = 6) -> str:
    names = [a.name for a in paper.authors]
    if len(names) > limit:
        return ", ".join(names[:limit]) + f" and {len(names) - limit} more"
    return ", ".join(names)


def _links(links: Links) -> dict[str, str]:
    return {k: str(v) for k, v in links.model_dump(exclude_none=True).items()}


def _board_button(
    b: board.Board, paper: Paper | PaperRef, *, found: dt.date, prereq_of: str | None = None
) -> dict[str, Any]:
    """What <board-button> needs: the paper's ids, the card it adds, and where it is now."""
    ref = ref_from_paper(paper) if isinstance(paper, Paper) else paper
    source = board.CardSource(
        kind=board.SourceKind.PREREQUISITE if prereq_of else board.SourceKind.PICK,
        date=found,
        pick_id=prereq_of,
    )
    card = b.find(ref.ids)
    return {
        "ids": ref.ids.model_dump(mode="json", exclude_none=True),
        "payload": {
            "paper": ref.model_dump(mode="json", exclude_none=True),
            "source": source.model_dump(mode="json", exclude_none=True),
        },
        "column": card.column.value if card else "",
    }


def pick(
    view: PickView, day: Day, *, root: str, topics: TopicsConfig, b: board.Board
) -> dict[str, Any]:
    """The data for one <paper-card>."""
    p = view.pick
    paper = p.paper
    graph = None
    if view.graph:
        graph = {
            "href": f"{root}days/{day.date.isoformat()}/graph/index.html#pick-{p.rank}",
            "count": len(view.graph.nodes) - 1,
        }
    return {
        "rank": p.rank,
        "topic_label": topic_label(topics, p.topic),
        "funnel": p.funnel.value,
        "status": paper.publication.status.value,
        "venue": paper.publication.venue,
        "repick": p.repick,
        "title": paper.title,
        "href": paper_link(paper.links),
        "authors": authors(paper),
        "reason": p.reason,
        "tldr": paper.tldr,
        "abstract": paper.abstract,
        "links": _links(paper.links),
        "board": _board_button(b, paper, found=day.date),
        "graph": graph,
        "prerequisites": [
            {
                "label": pre.label.value,
                "title": pre.paper.title,
                "href": paper_link(pre.paper.links),
                "year": pre.paper.year,
                "venue": pre.paper.venue,
                "reasons": pre.reasons,
                "quote": pre.quote,
                "tldr": pre.paper.tldr,
                "board": _board_button(b, pre.paper, found=day.date, prereq_of=paper.id),
            }
            for pre in view.prerequisites
        ],
    }


def archive(days: list[Day], *, root: str, topics: TopicsConfig) -> dict[str, Any]:
    """The data for <archive-list>."""
    return {
        "topics": [[tag, t.label] for tag, t in topics.topics.items()],
        "days": [
            {
                "label": long_date(day.date),
                "href": f"{root}days/{day.date.isoformat()}/index.html",
                "picks": [
                    {
                        "title": v.pick.paper.title,
                        "href": f"{root}days/{day.date.isoformat()}/index.html#pick-{v.pick.rank}",
                        "topic": v.pick.topic or "",
                        "topic_label": topic_label(topics, v.pick.topic),
                        "funnel": v.pick.funnel.value,
                    }
                    for v in day.picks
                ],
            }
            for day in days
        ],
    }


def board_data(b: board.Board) -> dict[str, Any]:
    """The build-time board for <reading-board>, in the same shape as data/board.json."""
    return b.model_dump(mode="json", exclude_none=True)


def _graph_node(placed: graph_view.PlacedNode) -> dict[str, Any]:
    paper = placed.node.paper
    return {
        "id": paper.id,
        "role": placed.node.role.value,
        "x": placed.x,
        "y": placed.y,
        "r": placed.r,
        "label": placed.label,
        "label_left": placed.label_left,
        "title": paper.title,
        "year": paper.year,
        "citations": paper.citations,
        "href": paper_link(paper.links),
    }


def graph(view: PickView, day: Day, *, root: str) -> dict[str, Any]:
    """The data for one <paper-graph>: graph_view's layout as plain JSON."""
    assert view.graph is not None
    lay = graph_view.layout(view.graph)
    p = view.pick
    return {
        "pick": {"rank": p.rank, "title": p.paper.title, "href": paper_link(p.paper.links)},
        "back_href": f"{root}days/{day.date.isoformat()}/index.html#pick-{p.rank}",
        "count": len(view.graph.nodes),
        "width": lay.width,
        "height": lay.height,
        "ticks": [{"label": t.label, "x": t.x} for t in lay.ticks],
        "edges": [
            {
                "source": e.edge.source,
                "target": e.edge.target,
                "x1": e.x1,
                "y1": e.y1,
                "x2": e.x2,
                "y2": e.y2,
                "strong": e.touches_pick,
                "builds_on": e.edge.kind is EdgeKind.BUILDS_ON,
            }
            for e in lay.edges
        ],
        "nodes": [_graph_node(n) for n in lay.nodes],
        "groups": [
            {
                "name": name,
                "nodes": [
                    {
                        "title": n.paper.title,
                        "href": paper_link(n.paper.links),
                        "year": n.paper.year,
                        "citations": n.paper.citations,
                    }
                    for n in nodes
                ],
            }
            for name, nodes in lay.groups
        ],
    }
