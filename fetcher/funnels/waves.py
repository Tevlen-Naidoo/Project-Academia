"""Waves funnel: older papers (up to 18 months) that are gathering citations fast."""

from __future__ import annotations

import calendar
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
_NEW_ARXIV_RE = re.compile(r"^(?P<yy>\d{2})(?P<mm>\d{2})\.\d{4,5}$")


def first_seen_by(work: OAWork) -> dt.date | None:
    """The latest date by which the work had surely appeared, from evidence besides its
    publication date: the end of its arXiv id's month, or the end of the first year it was cited.

    OpenAlex dates a reprint, or the journal version of an old preprint, by its new
    publication, so a decades-old paper can look like it is making waves.
    """
    dates: list[dt.date] = []
    match = _NEW_ARXIV_RE.match(work.arxiv_id or "")
    if match and 1 <= int(match["mm"]) <= 12:
        year, month = 2000 + int(match["yy"]), int(match["mm"])
        dates.append(dt.date(year, month, calendar.monthrange(year, month)[1]))
    cited = [c.year for c in work.counts_by_year if c.cited_by_count > 0]
    if cited:
        dates.append(dt.date(min(cited), 12, 31))
    return min(dates, default=None)


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
    """A candidate from an OpenAlex work, or None if it has no title, abstract or topic,
    or is older than the oldest window under a new publication date."""
    seen = first_seen_by(work)
    if seen is not None and seen < windows(today)[-1].since:
        return None
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
