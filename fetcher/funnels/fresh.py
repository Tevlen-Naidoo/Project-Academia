"""Fresh funnel: papers submitted to arXiv in the last few days."""

from __future__ import annotations

import datetime as dt
import re

from fetcher.models import (
    Author,
    Funnel,
    Links,
    Paper,
    PaperDates,
    PaperIds,
    Publication,
    PublicationStatus,
    StatusEvidence,
)
from fetcher.sources.arxiv import ArxivEntry
from fetcher.topics import TopicsConfig

_VENUE_WORD = r"(?:[A-Z][A-Za-z&\-]*|'?\d{2,4})"
_ACCEPTED_RE = re.compile(
    r"\b(?i:accepted|to appear|camera[- ]ready|published)\b"
    r"[^.;]{0,40}?\b(?i:at|in|to|by|for)\s+(?:the\s+)?"
    rf"(?P<venue>[A-Z][A-Za-z&\-]*(?:\s+(?:(?:of|on|and|for|the)\s+)*{_VENUE_WORD})*)"
)
_DOI_RE = re.compile(r"^10\.\S+/\S+$")


def venue_from_comment(comment: str | None) -> str | None:
    """`"Accepted at NeurIPS 2026. 12 pages"` -> `"NeurIPS 2026"`."""
    if not comment:
        return None
    match = _ACCEPTED_RE.search(comment)
    return match["venue"].strip() if match else None


def publication_from_entry(entry: ArxivEntry) -> Publication:
    if entry.journal_ref:
        return Publication(
            status=PublicationStatus.PUBLISHED,
            venue=entry.journal_ref,
            evidence=StatusEvidence.ARXIV_JOURNAL_REF,
        )
    if entry.doi and _DOI_RE.match(entry.doi):
        return Publication(status=PublicationStatus.PUBLISHED, evidence=StatusEvidence.ARXIV_DOI)
    venue = venue_from_comment(entry.comment)
    if venue:
        return Publication(
            status=PublicationStatus.ACCEPTED, venue=venue, evidence=StatusEvidence.ARXIV_COMMENT
        )
    return Publication()


def paper_from_entry(
    entry: ArxivEntry, *, topics: TopicsConfig, today: dt.date, now: dt.datetime
) -> Paper | None:
    """A candidate `Paper`, or None if the entry maps to none of the configured topics."""
    tags = topics.tag(arxiv_categories=entry.categories, title=entry.title, abstract=entry.abstract)
    if not tags:
        return None
    doi = entry.doi if entry.doi and _DOI_RE.match(entry.doi) else None
    ids = PaperIds(arxiv=entry.arxiv_id, doi=doi)
    return Paper(
        id=ids.canonical(),
        ids=ids,
        title=entry.title,
        authors=[Author(name=a.name, affiliation=a.affiliation) for a in entry.authors],
        abstract=entry.abstract,
        topics=tags,
        source_categories=list(entry.categories),
        dates=PaperDates(first_seen=today, submitted=entry.submitted.date()),
        publication=publication_from_entry(entry),
        links=Links.model_validate(
            {
                "abstract_page": f"https://arxiv.org/abs/{entry.arxiv_id}",
                "pdf": f"https://arxiv.org/pdf/{entry.arxiv_id}",
                "doi": f"https://doi.org/{doi}" if doi else None,
            }
        ),
        funnel=[Funnel.FRESH],
        fetched_at=now,
    )
