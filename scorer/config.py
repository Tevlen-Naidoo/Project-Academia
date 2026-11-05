"""Load config/scoring.yaml."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fetcher.models import Funnel, PublicationStatus

DEFAULT_SCORING_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.yaml"

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

Unit = Annotated[float, Field(ge=0, le=1)]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Weights(_Config):
    buzz: Unit
    status: Unit
    citation_speed: Unit
    influential: Unit
    recency: Unit

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = sum(self.model_dump().values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"weights should add up to 1, not {total:g}")
        return self


class TopicSpread(_Config):
    days: Annotated[int, Field(ge=0)]
    factor: Unit


class Repeats(_Config):
    cooldown_days: Annotated[int, Field(ge=0)]
    factor: Unit


class ScoringConfig(_Config):
    version: Literal[1]
    picks_per_day: Annotated[int, Field(ge=1, le=10)]
    weights: dict[Funnel, Weights]
    status_values: dict[PublicationStatus, Unit]
    recency_days: Annotated[int, Field(ge=1)]
    light_days: tuple[Weekday, ...] = ()
    topic_spread: TopicSpread
    repeats: Repeats

    @model_validator(mode="after")
    def _complete(self) -> Self:
        missing = [f.value for f in Funnel if f not in self.weights]
        missing += [s.value for s in PublicationStatus if s not in self.status_values]
        if missing:
            raise ValueError(f"missing settings for: {', '.join(missing)}")
        return self


def load_scoring(path: Path = DEFAULT_SCORING_PATH) -> ScoringConfig:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ScoringConfig.model_validate(raw)
