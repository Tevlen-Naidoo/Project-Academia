"""The picks file: data/picks/YYYY-MM-DD.json, read by the web app.

Each pick embeds the full `fetcher.models.Paper`, so the app needs only this
file for a day. The schema is exported to schema/picks.schema.json.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fetcher.models import Funnel, Paper

SCHEMA_VERSION: Literal[1] = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Signal(StrEnum):
    BUZZ = "buzz"
    STATUS = "status"
    CITATION_SPEED = "citation_speed"
    INFLUENTIAL = "influential"
    RECENCY = "recency"


class Slot(StrEnum):
    OPEN = "open"
    """Any paper, chosen by score."""
    LIGHT = "light"
    """Held for a light topic (astronomy, history, art, culture) on light days."""


class SignalScore(_Model):
    signal: Signal
    value: float
    """The signal as a number from 0 to 1 (a percentile, or the status value)."""
    weight: Annotated[float, Field(ge=0, le=1)]
    known: bool = True
    """False when the source had no data and the median (0.5) was used."""


class Pick(_Model):
    rank: Annotated[int, Field(ge=1)]
    slot: Slot
    topic: str | None
    """The paper's main topic: its first tag."""
    funnel: Funnel
    """Which funnel's weights gave the score."""
    score: Annotated[float, Field(ge=0, le=1)]
    adjusted: Annotated[float, Field(ge=0, le=1)]
    """The score after the topic-spread and repeat factors."""
    breakdown: list[SignalScore]
    reason: Annotated[str, Field(min_length=1)]
    """One line for the web app, e.g. "Accepted at CVPR 2026 · 142 upvotes on Hugging Face"."""
    repick: bool = False
    """True when this paper was picked before, more than the cooldown ago."""
    paper: Paper


class PicksFile(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    date: dt.date
    generated_at: AwareDatetime
    candidates_file: str
    """The candidates file these picks came from, relative to data/."""
    light_day: bool
    notes: list[str] = Field(default_factory=list)
    """Why there are fewer picks than asked for, or which fallback was used."""
    picks: list[Pick] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [p.paper.id for p in self.picks]
        if len(set(ids)) != len(ids):
            raise ValueError("a paper is picked twice")
        if [p.rank for p in self.picks] != list(range(1, len(self.picks) + 1)):
            raise ValueError("ranks should run 1, 2, 3, ... in order")
        return self
