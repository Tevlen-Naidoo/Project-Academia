"""The shared data contract: every paper the fetcher produces is a `Paper`.

Step 2 (scoring) and step 3 (web app) read these models, either directly in
Python or through the JSON Schema exported to `schema/`. Missing numbers are
`None`, never 0, so consumers can tell "no citations" from "unknown".
"""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    NonNegativeFloat,
    NonNegativeInt,
    model_validator,
)

SCHEMA_VERSION: Literal[1] = 1

TopicTag = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
"""An internal topic tag such as `vision` or `graph-theory`, defined in config/topics.yaml."""

ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}|[a-z-]+(\.[A-Z]{2})?/\d{7})$")
"""New-style (2410.12345) or old-style (hep-th/9901001) arXiv ids, without version."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PublicationStatus(StrEnum):
    PUBLISHED = "published"
    """In a journal or conference proceedings."""
    ACCEPTED = "accepted"
    """Venue known, not yet in proceedings."""
    PREPRINT = "preprint"


class VenueType(StrEnum):
    JOURNAL = "journal"
    CONFERENCE = "conference"
    WORKSHOP = "workshop"
    OTHER = "other"


class StatusEvidence(StrEnum):
    """Which signal decided `Publication.status`, strongest first."""

    S2_VENUE = "s2_venue"
    S2_PUBLICATION_TYPE = "s2_publication_type"
    OPENALEX_PUBLISHED_VERSION = "openalex_published_version"
    ARXIV_JOURNAL_REF = "arxiv_journal_ref"
    ARXIV_DOI = "arxiv_doi"
    NONE = "none"


class Funnel(StrEnum):
    FRESH = "fresh"
    """New in the last few days."""
    WAVES = "waves"
    """Older, but gaining citations or buzz quickly."""


class PaperIds(_Model):
    arxiv: Annotated[str, Field(pattern=ARXIV_ID_RE.pattern)] | None = None
    doi: Annotated[str, Field(pattern=r"^10\.\S+/\S+$")] | None = None
    s2: str | None = None
    openalex: Annotated[str, Field(pattern=r"^W\d+$")] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        if not any((self.arxiv, self.doi, self.s2, self.openalex)):
            raise ValueError("a paper needs at least one id")
        return self

    def canonical(self) -> str:
        """The stable id used as `Paper.id`: arXiv, then DOI, then OpenAlex, then S2."""
        if self.arxiv:
            return f"arxiv:{self.arxiv}"
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.openalex:
            return f"openalex:{self.openalex}"
        assert self.s2 is not None  # guaranteed by _at_least_one
        return f"s2:{self.s2}"


class Author(_Model):
    name: Annotated[str, Field(min_length=1)]
    affiliation: str | None = None


class PaperDates(_Model):
    first_seen: dt.date
    """The day the fetcher first saw this paper."""
    submitted: dt.date | None = None
    """First arXiv version, or the earliest date any source gives."""
    published: dt.date | None = None
    """Date of the published version, if any."""


class Publication(_Model):
    status: PublicationStatus = PublicationStatus.PREPRINT
    venue: str | None = None
    venue_type: VenueType | None = None
    evidence: StatusEvidence = StatusEvidence.NONE

    @model_validator(mode="after")
    def _evidence_matches_status(self) -> Self:
        is_preprint = self.status is PublicationStatus.PREPRINT
        has_evidence = self.evidence is not StatusEvidence.NONE
        if is_preprint == has_evidence:
            raise ValueError("evidence must be 'none' exactly when status is 'preprint'")
        return self


class Citations(_Model):
    total: NonNegativeInt | None = None
    influential: NonNegativeInt | None = None
    last_12_months: NonNegativeInt | None = None
    per_month: NonNegativeFloat | None = None
    """Citations per month since publication."""
    by_year: dict[int, NonNegativeInt] = Field(default_factory=dict)


class Buzz(_Model):
    hf_upvotes: NonNegativeInt | None = None
    hf_comments: NonNegativeInt | None = None
    hn_points: NonNegativeInt | None = None
    hn_comments: NonNegativeInt | None = None
    awards: list[str] = Field(default_factory=list)


class Links(_Model):
    abstract_page: HttpUrl | None = None
    pdf: HttpUrl | None = None
    doi: HttpUrl | None = None
    code: HttpUrl | None = None


class Paper(_Model):
    id: Annotated[str, Field(pattern=r"^(arxiv|doi|openalex|s2):.+$")]
    ids: PaperIds
    title: Annotated[str, Field(min_length=1)]
    authors: list[Author] = Field(default_factory=list)
    abstract: Annotated[str, Field(min_length=1)]
    tldr: str | None = None
    topics: list[TopicTag] = Field(default_factory=list)
    source_categories: list[str] = Field(default_factory=list)
    """Raw categories from the source, e.g. arXiv `cs.CV`."""
    dates: PaperDates
    publication: Publication = Field(default_factory=Publication)
    citations: Citations = Field(default_factory=Citations)
    buzz: Buzz = Field(default_factory=Buzz)
    links: Links = Field(default_factory=Links)
    funnel: Annotated[list[Funnel], Field(min_length=1)]
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def _id_is_canonical(self) -> Self:
        expected = self.ids.canonical()
        if self.id != expected:
            raise ValueError(f"id {self.id!r} should be the canonical id {expected!r}")
        return self


class SourceReport(_Model):
    fetched: NonNegativeInt = 0
    errors: list[str] = Field(default_factory=list)
    duration_s: NonNegativeFloat = 0.0


class CandidatesFile(_Model):
    """One day's output, saved as data/candidates/YYYY-MM-DD.json."""

    schema_version: Literal[1] = SCHEMA_VERSION
    date: dt.date
    generated_at: AwareDatetime
    sources: dict[str, SourceReport] = Field(default_factory=dict)
    papers: list[Paper] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        seen: set[str] = set()
        for paper in self.papers:
            if paper.id in seen:
                raise ValueError(f"duplicate paper id {paper.id}")
            seen.add(paper.id)
        return self
