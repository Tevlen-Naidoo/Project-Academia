"""Hugging Face Daily Papers: community upvotes on (mostly ML) arXiv papers.

Endpoint: https://huggingface.co/api/daily_papers?date=YYYY-MM-DD
Public and unauthenticated; we make one call per day in the window.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fetcher.http import HttpClient, RateLimiter

DAILY_URL = "https://huggingface.co/api/daily_papers"
REQUEST_INTERVAL_S = 1.0


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class HFPaper(_Lenient):
    id: str
    """The arXiv id."""
    upvotes: int = 0


class HFDailyItem(_Lenient):
    paper: HFPaper
    num_comments: int = Field(default=0, alias="numComments")


@dataclass(frozen=True, slots=True)
class HFBuzz:
    upvotes: int
    comments: int


@dataclass(slots=True)
class HFResult:
    buzz: dict[str, HFBuzz] = field(default_factory=dict)
    """Keyed by arXiv id. A paper featured twice keeps its highest counts."""
    errors: list[str] = field(default_factory=list)


class HuggingFaceSource:
    def __init__(self, http: HttpClient, *, limiter: RateLimiter | None = None) -> None:
        self._http = http
        self._limiter = limiter or RateLimiter(REQUEST_INTERVAL_S)

    def recent(self, *, until: dt.date, days: int) -> HFResult:
        result = HFResult()
        for offset in range(days):
            day = until - dt.timedelta(days=offset)
            try:
                response = self._http.get(
                    DAILY_URL, limiter=self._limiter, params={"date": day.isoformat()}
                )
                rows = response.json()
            except Exception as exc:  # one missing day must not lose the rest
                result.errors.append(f"{day}: {type(exc).__name__}: {exc}")
                continue
            if not isinstance(rows, list):
                result.errors.append(f"{day}: unexpected response")
                continue
            for row in rows:
                try:
                    item = HFDailyItem.model_validate(row)
                except ValidationError:
                    continue
                old = result.buzz.get(item.paper.id)
                result.buzz[item.paper.id] = HFBuzz(
                    upvotes=max(item.paper.upvotes, old.upvotes if old else 0),
                    comments=max(item.num_comments, old.comments if old else 0),
                )
        return result
