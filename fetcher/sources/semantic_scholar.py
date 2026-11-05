"""Semantic Scholar Graph API: citations, venue and TLDR for known papers.

Docs: https://api.semanticscholar.org/api-docs/graph
Limits: with a free key, 1 request per second; without one, a pool shared by
all anonymous users, so we stay at 1 per second either way.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fetcher.http import HttpClient, RateLimiter

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
BATCH_SIZE = 250
"""The API allows 500, but large batches can exceed its response size limit."""
MIN_SPLIT = 8
"""Stop halving a rejected batch below this many ids."""
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
        for start in range(0, len(ids), self._batch_size):
            self._fetch_chunk(list(ids[start : start + self._batch_size]), start, result)
        return result

    def _fetch_chunk(self, chunk: list[str], start: int, result: S2Result) -> None:
        """Fetch one chunk. A rejected chunk is split in half and retried, because the
        API answers 400 when a batch's response would be too large or one id is bad."""
        headers = {"x-api-key": self._api_key} if self._api_key else None
        problem: str
        try:
            response = self._http.post(
                BATCH_URL,
                limiter=self._limiter,
                params={"fields": FIELDS},
                headers=headers,
                json={"ids": chunk},
            )
            rows = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                result.errors.append(f"batch at {start}: HTTP {exc.response.status_code}")
                return
            problem = f"HTTP 400: {_snippet(exc.response.text)}"
        except Exception as exc:  # network, retries exhausted, bad JSON: report and move on
            result.errors.append(f"batch at {start}: {type(exc).__name__}: {exc}")
            return
        else:
            if isinstance(rows, list) and len(rows) == len(chunk):
                self._collect(chunk, rows, result)
                return
            problem = f"unexpected response: {_snippet(str(rows))}"

        if len(chunk) > MIN_SPLIT:
            half = len(chunk) // 2
            self._fetch_chunk(chunk[:half], start, result)
            self._fetch_chunk(chunk[half:], start + half, result)
        else:
            result.errors.append(f"batch at {start} ({len(chunk)} ids): {problem}")

    @staticmethod
    def _collect(chunk: list[str], rows: list[object], result: S2Result) -> None:
        for asked, row in zip(chunk, rows, strict=True):
            if row is None:
                continue  # not in Semantic Scholar (yet)
            try:
                result.papers[asked] = S2Paper.model_validate(row)
            except ValidationError as exc:
                result.errors.append(f"{asked}: {exc.error_count()} validation errors")


def _snippet(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
