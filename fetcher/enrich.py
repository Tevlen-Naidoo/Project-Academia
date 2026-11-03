"""Merge Semantic Scholar and OpenAlex data into candidates, and derive signals.

Published-status evidence, strongest first: a Semantic Scholar venue, an
OpenAlex published version at a journal or conference, then what arXiv says.
A stronger signal replaces a weaker one; a missing signal never downgrades.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence

from fetcher.models import (
    Citations,
    Paper,
    Publication,
    PublicationStatus,
    StatusEvidence,
    VenueType,
)
from fetcher.sources.openalex import OAWork, arxiv_doi
from fetcher.sources.semantic_scholar import S2Paper, lookup_id
from fetcher.topics import TopicsConfig

EVIDENCE_RANK: dict[StatusEvidence, int] = {
    StatusEvidence.S2_VENUE: 6,
    StatusEvidence.S2_PUBLICATION_TYPE: 5,
    StatusEvidence.OPENALEX_PUBLISHED_VERSION: 4,
    StatusEvidence.ARXIV_JOURNAL_REF: 3,
    StatusEvidence.ARXIV_DOI: 2,
    StatusEvidence.ARXIV_COMMENT: 1,
    StatusEvidence.NONE: 0,
}
PREPRINT_VENUES = frozenset({"arxiv", "arxiv.org", "biorxiv", "medrxiv", "ssrn", "research square"})
S2_PUBLISHED_TYPES = frozenset({"JournalArticle", "Conference"})
OA_PUBLISHED_SOURCE_TYPES = frozenset({"journal", "conference"})
ARXIV_DOI_PREFIX = "10.48550/"


def _is_preprint_venue(name: str | None) -> bool:
    return not name or name.strip().lower() in PREPRINT_VENUES


def _venue_type(raw: str | None) -> VenueType | None:
    if not raw:
        return None
    raw = raw.lower()
    if "journal" in raw:
        return VenueType.JOURNAL
    if "conference" in raw:
        return VenueType.CONFERENCE
    if "workshop" in raw:
        return VenueType.WORKSHOP
    return VenueType.OTHER


def _parse_date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _upgrade(paper: Paper, candidate: Publication) -> None:
    if EVIDENCE_RANK[candidate.evidence] > EVIDENCE_RANK[paper.publication.evidence]:
        paper.publication = candidate


def s2_publication(s2: S2Paper) -> Publication | None:
    venue_name = (s2.publication_venue.name if s2.publication_venue else None) or s2.venue
    if not _is_preprint_venue(venue_name):
        return Publication(
            status=PublicationStatus.PUBLISHED,
            venue=venue_name,
            venue_type=_venue_type(s2.publication_venue.type if s2.publication_venue else None),
            evidence=StatusEvidence.S2_VENUE,
        )
    types = set(s2.publication_types or ())
    journal = s2.journal.name if s2.journal else None
    if types & S2_PUBLISHED_TYPES and not _is_preprint_venue(journal):
        return Publication(
            status=PublicationStatus.PUBLISHED,
            venue=journal,
            venue_type=VenueType.JOURNAL if "JournalArticle" in types else VenueType.CONFERENCE,
            evidence=StatusEvidence.S2_PUBLICATION_TYPE,
        )
    return None


def apply_s2(paper: Paper, s2: S2Paper) -> None:
    paper.ids.s2 = s2.paper_id
    doi = s2.doi
    if doi and not paper.ids.doi and not doi.lower().startswith(ARXIV_DOI_PREFIX):
        paper.ids.doi = doi
        paper.id = paper.ids.canonical()
    paper.citations.total = s2.citation_count
    paper.citations.influential = s2.influential_citation_count
    if s2.tldr and s2.tldr.text:
        paper.tldr = s2.tldr.text
    publication = s2_publication(s2)
    if publication is not None:
        _upgrade(paper, publication)
        published = _parse_date(s2.publication_date)
        if published and paper.dates.published is None:
            paper.dates.published = published


def oa_publication(work: OAWork) -> Publication | None:
    for location in work.locations:
        source = location.source
        if (
            location.version == "publishedVersion"
            and source is not None
            and source.type in OA_PUBLISHED_SOURCE_TYPES
            and not _is_preprint_venue(source.display_name)
        ):
            return Publication(
                status=PublicationStatus.PUBLISHED,
                venue=source.display_name,
                venue_type=_venue_type(source.type),
                evidence=StatusEvidence.OPENALEX_PUBLISHED_VERSION,
            )
    return None


def apply_openalex(paper: Paper, work: OAWork, topics: TopicsConfig) -> None:
    paper.ids.openalex = work.work_id
    paper.citations.by_year = {c.year: c.cited_by_count for c in work.counts_by_year}
    if paper.citations.total is None:
        paper.citations.total = work.cited_by_count
    publication = oa_publication(work)
    if publication is not None:
        _upgrade(paper, publication)
    extra = topics.tag(
        openalex_subfields=work.subfield_ids, title=paper.title, abstract=paper.abstract
    )
    paper.topics = list(dict.fromkeys([*paper.topics, *extra]))


def derive_signals(paper: Paper, today: dt.date) -> None:
    """Citations per month since the paper first appeared, and an estimate of the last 12 months."""
    c: Citations = paper.citations
    known = [d for d in (paper.dates.submitted, paper.dates.published) if d is not None]
    start = min(known) if known else None  # citations accrue from first appearance
    if c.total is not None and start is not None:
        months = max((today - start).days / 30.44, 1.0)
        c.per_month = round(c.total / months, 3)
    if c.by_year:
        this_year = c.by_year.get(today.year, 0)
        fraction_left = 1 - (today.timetuple().tm_yday / 365.25)
        last_year = c.by_year.get(today.year - 1, 0) * fraction_left
        c.last_12_months = round(this_year + last_year)


def presort_key(paper: Paper) -> tuple[int, int, float, dt.date]:
    """Rough order used only to trim the pool; step 2 does the real scoring."""
    status = {
        PublicationStatus.PUBLISHED: 2,
        PublicationStatus.ACCEPTED: 1,
        PublicationStatus.PREPRINT: 0,
    }[paper.publication.status]
    return (
        paper.citations.influential or 0,
        status,
        paper.citations.per_month or 0.0,
        paper.dates.submitted or dt.date.min,
    )


def select_candidates(papers: Sequence[Paper], *, per_topic: int, cap: int) -> list[Paper]:
    """The best `per_topic` papers of every topic, so small topics are not crowded out."""
    ranked = sorted(papers, key=presort_key, reverse=True)
    chosen: dict[str, Paper] = {}
    counts: dict[str, int] = {}
    for paper in ranked:
        if len(chosen) >= cap:
            break
        if any(counts.get(t, 0) < per_topic for t in paper.topics):
            chosen[paper.id] = paper
            for t in paper.topics:
                counts[t] = counts.get(t, 0) + 1
    return list(chosen.values())


def s2_ids(papers: Iterable[Paper]) -> dict[str, Paper]:
    out: dict[str, Paper] = {}
    for paper in papers:
        key = lookup_id(arxiv=paper.ids.arxiv, doi=paper.ids.doi)
        if key:
            out[key] = paper
    return out


def openalex_dois(papers: Iterable[Paper]) -> dict[str, Paper]:
    out: dict[str, Paper] = {}
    for paper in papers:
        if paper.ids.doi:
            out[paper.ids.doi.lower()] = paper
        if paper.ids.arxiv:
            out[arxiv_doi(paper.ids.arxiv)] = paper
    return out
