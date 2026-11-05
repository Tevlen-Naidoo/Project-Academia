"""The prerequisite reading attached to each daily pick.

Saved as data/prereqs/YYYY-MM-DD.json and exported to schema/ for the web app.
Ids follow the fetcher's rules (`fetcher.models.PaperIds.canonical`), so a
prerequisite and a pick for the same paper share one id.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    model_validator,
)

from fetcher.models import Links, PaperIds

SCHEMA_VERSION: Literal[1] = 1

PaperId = Annotated[str, Field(pattern=r"^(arxiv|doi|openalex|s2):.+$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PrereqLabel(StrEnum):
    READ_FIRST = "read_first"
    """The pick builds directly on it: read it before the pick."""
    FOUNDATION = "foundation"
    """The lowest paper under a Read first paper's own chain of Read first papers."""
    BACKGROUND = "background"
    """Useful context, not required."""


class ReferenceSource(StrEnum):
    S2 = "s2"
    OPENALEX = "openalex"
    NONE = "none"


class PaperRef(_Model):
    """A slim record of a referenced paper: enough to show it and link to it."""

    id: PaperId
    ids: PaperIds
    title: Annotated[str, Field(min_length=1)]
    year: int | None = None
    venue: str | None = None
    citations: NonNegativeInt | None = None
    influential_citations: NonNegativeInt | None = None
    is_review: bool = False
    tldr: str | None = None
    links: Links = Field(default_factory=Links)

    @model_validator(mode="after")
    def _id_is_canonical(self) -> Self:
        expected = self.ids.canonical()
        if self.id != expected:
            raise ValueError(f"id {self.id!r} should be the canonical id {expected!r}")
        return self


class Prerequisite(_Model):
    paper: PaperRef
    label: PrereqLabel
    score: float
    reasons: list[str] = Field(default_factory=list)
    """Short, human-readable reasons, e.g. "Influential reference, cited for its method"."""
    quote: str | None = None
    """A sentence from the pick where it cites this paper, when S2 has one."""
    supports: list[PaperId] = Field(min_length=1)
    """The picks this paper is a prerequisite for."""
    chain: list[PaperId] = Field(default_factory=list)
    """For a Foundation: the Read first paper it explains, then each paper down to this one."""

    @model_validator(mode="after")
    def _chain_only_on_foundation(self) -> Self:
        if (self.label is PrereqLabel.FOUNDATION) != bool(self.chain):
            raise ValueError("chain is set exactly when the label is 'foundation'")
        return self


class PickPrereqs(_Model):
    pick_id: PaperId
    pick_title: str
    source: ReferenceSource
    """Where the reference list came from."""
    references_seen: NonNegativeInt = 0
    prerequisites: list[Prerequisite] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PrereqsFile(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    date: dt.date
    generated_at: AwareDatetime
    picks: list[PickPrereqs] = Field(default_factory=list)

    def shown_ids(self) -> set[str]:
        """Every paper this file showed, picks included."""
        out = {p.pick_id for p in self.picks}
        out.update(pre.paper.id for p in self.picks for pre in p.prerequisites)
        return out
