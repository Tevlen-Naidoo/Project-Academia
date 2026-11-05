"""Waves funnel: older papers (up to 18 months) that are gathering citations fast."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from fetcher import enrich
from fetcher.models import Author, Funnel, Links, Paper, PaperDates, PaperIds
from fetcher.sources.openalex import OAWork
from fetcher.topics import TopicsConfig


@dataclass(frozen=True, slots=True)
class Window:
    """A publication-date window and the citations a paper needs to count as rising in it."""

    name: str
    since: dt.date
    until: dt.date
    min_citations: int


def windows(today: dt.date) -> list[Window]:
    """Two windows, so recent papers are not crowded out by ones that had longer to collect."""
    return [
        Window("0-6 months", today - dt.timedelta(days=182), today, min_citations=5),
        Window(
            "6-18 months",
            today - dt.timedelta(days=548),
            today - dt.timedelta(days=183),
            min_citations=20,
        ),
    ]


_DOI_RE = re.compile(r"^10\.\S+/\S+$")


def _date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def paper_from_work(
    work: OAWork, *, topics: TopicsConfig, today: dt.date, now: dt.datetime
) -> Paper | None:
    """A candidate from an OpenAlex work, or None if it has no title, abstract or topic."""
    title = " ".join((work.title or "").split())
    abstract = work.abstract()
    if not title or not abstract:
        return None
    subfields = list(work.subfield_ids)
    if work.primary_topic and work.primary_topic.subfield:
        tail = work.primary_topic.subfield.id.rsplit("/", 1)[-1]
        if tail.isdigit():
            subfields.append(int(tail))
    tags = topics.tag(openalex_subfields=subfields, title=title, abstract=abstract)
    if not tags:
        return None

    arxiv = work.arxiv_id
    doi = work.bare_doi
    if doi and (doi.startswith(enrich.ARXIV_DOI_PREFIX) or not _DOI_RE.match(doi)):
        doi = None  # arXiv's own DOI, or a malformed one (spaces, missing suffix)
    ids = PaperIds(arxiv=arxiv, doi=doi, openalex=work.work_id)
    when = _date(work.publication_date)
    is_preprint = work.type == "preprint"
    links: dict[str, str | None] = {
        "abstract_page": f"https://arxiv.org/abs/{arxiv}" if arxiv else None,
        "pdf": f"https://arxiv.org/pdf/{arxiv}" if arxiv else None,
        "doi": f"https://doi.org/{doi}" if doi else None,
    }
    if links["abstract_page"] is None:
        links["abstract_page"] = links["doi"]

    paper = Paper(
        id=ids.canonical(),
        ids=ids,
        title=title,
        authors=[
            Author(name=a.author.display_name)
            for a in work.authorships
            if a.author and a.author.display_name
        ],
        abstract=abstract,
        topics=tags,
        dates=PaperDates(
            first_seen=today,
            submitted=when if is_preprint else None,
            published=None if is_preprint else when,
        ),
        links=Links.model_validate(links),
        funnel=[Funnel.WAVES],
        fetched_at=now,
    )
    enrich.apply_openalex(paper, work, topics)
    return paper
