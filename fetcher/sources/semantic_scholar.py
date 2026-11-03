"""Semantic Scholar Graph API: citations, venue and TLDR for known papers.

Docs: https://api.semanticscholar.org/api-docs/graph
Limits: with a free key, 1 request per second; without one, a pool shared by
all anonymous users, so we stay at 1 per second either way.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fetcher.http import HttpClient, RateLimiter

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
BATCH_SIZE = 500
REQUEST_INTERVAL_S = 1.1
FIELDS = ",".join(
    [
        "paperId",
        "externalIds",
        "venue",
        "publicationVenue",
        "publicationTypes",
        "journal",
        "publicationDate",
        "citationCount",
        "influentialCitationCount",
        "tldr",
    ]
)


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class S2Venue(_Lenient):
    name: str | None = None
    type: str | None = None


class S2Journal(_Lenient):
    name: str | None = None


class S2Tldr(_Lenient):
    text: str | None = None


class S2Paper(_Lenient):
    paper_id: str = Field(alias="paperId")
    external_ids: dict[str, str | int] = Field(default_factory=dict, alias="externalIds")
    venue: str | None = None
    publication_venue: S2Venue | None = Field(default=None, alias="publicationVenue")
    publication_types: list[str] | None = Field(default=None, alias="publicationTypes")
    journal: S2Journal | None = None
    publication_date: str | None = Field(default=None, alias="publicationDate")
    citation_count: int | None = Field(default=None, alias="citationCount")
    influential_citation_count: int | None = Field(default=None, alias="influentialCitationCount")
    tldr: S2Tldr | None = None

    @property
    def doi(self) -> str | None:
        value = self.external_ids.get("DOI")
        return str(value) if value else None


@dataclass(slots=True)
class S2Result:
    papers: dict[str, S2Paper] = field(default_factory=dict)
    """Keyed by the id we asked with, e.g. `ARXIV:2410.12345`."""
    requested: int = 0
    errors: list[str] = field(default_factory=list)


def lookup_id(*, arxiv: str | None, doi: str | None) -> str | None:
    if arxiv:
        return f"ARXIV:{arxiv}"
    if doi:
        return f"DOI:{doi}"
    return None


class SemanticScholarSource:
    def __init__(
        self,
        http: HttpClient,
        *,
        api_key: str | None = None,
        limiter: RateLimiter | None = None,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._http = http
        self._api_key = api_key if api_key is not None else os.environ.get("S2_API_KEY") or None
        self._limiter = limiter or RateLimiter(REQUEST_INTERVAL_S)
        self._batch_size = batch_size

    def batch(self, ids: Sequence[str]) -> S2Result:
        result = S2Result(requested=len(ids))
        headers = {"x-api-key": self._api_key} if self._api_key else None
        for start in range(0, len(ids), self._batch_size):
            chunk = list(ids[start : start + self._batch_size])
            try:
                response = self._http.post(
                    BATCH_URL,
                    limiter=self._limiter,
                    params={"fields": FIELDS},
                    headers=headers,
                    json={"ids": chunk},
                )
                rows = response.json()
            except Exception as exc:  # report and move on to the next batch
                result.errors.append(f"batch at {start}: {type(exc).__name__}: {exc}")
                continue
            if not isinstance(rows, list) or len(rows) != len(chunk):
                result.errors.append(f"batch at {start}: unexpected response shape")
                continue
            for asked, row in zip(chunk, rows, strict=True):
                if row is None:
                    continue  # not in Semantic Scholar (yet)
                try:
                    result.papers[asked] = S2Paper.model_validate(row)
                except ValidationError as exc:
                    result.errors.append(f"{asked}: {exc.error_count()} validation errors")
        return result
