"""OpenAlex works API: citations by year, published versions and topics.

Docs: https://developers.openalex.org
Free budget: $0.10/day with no key (about 1,000 list calls), $1/day with a
free key. Lookups here are list calls filtered by DOI, 50 DOIs per call.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import Field, ValidationError

from fetcher.http import HttpClient, RateLimiter
from fetcher.models import ARXIV_ID_RE
from fetcher.sources.base import LenientModel, Source, SourceResult

WORKS_URL = "https://api.openalex.org/works"
DOIS_PER_CALL = 50
REQUEST_INTERVAL_S = 0.2
SELECT = ",".join(
    [
        "id",
        "doi",
        "title",
        "publication_date",
        "cited_by_count",
        "counts_by_year",
        "locations",
        "topics",
        "abstract_inverted_index",
        "authorships",
        "primary_topic",
        "type",
    ]
)
WAVES_PER_QUERY = 100
"""OpenAlex's maximum page size."""


class OASource(LenientModel):
    display_name: str | None = None
    type: str | None = None


class OALocation(LenientModel):
    version: str | None = None
    landing_page_url: str | None = None
    source: OASource | None = None


class OAAuthor(LenientModel):
    display_name: str | None = None


class OAAuthorship(LenientModel):
    author: OAAuthor | None = None


class OACount(LenientModel):
    year: int
    cited_by_count: int = 0


class OASubfield(LenientModel):
    id: str


class OATopic(LenientModel):
    subfield: OASubfield | None = None


class OAWork(LenientModel):
    id: str
    doi: str | None = None
    title: str | None = None
    type: str | None = None
    authorships: list[OAAuthorship] = Field(default_factory=list)
    primary_topic: OATopic | None = None
    publication_date: str | None = None
    cited_by_count: int | None = None
    counts_by_year: list[OACount] = Field(default_factory=list)
    locations: list[OALocation] = Field(default_factory=list)
    topics: list[OATopic] = Field(default_factory=list)
    abstract_inverted_index: dict[str, list[int]] | None = None

    @property
    def work_id(self) -> str:
        return self.id.rsplit("/", 1)[-1]

    @property
    def bare_doi(self) -> str | None:
        if not self.doi:
            return None
        return self.doi.lower().removeprefix("https://doi.org/")

    @property
    def arxiv_id(self) -> str | None:
        """The arXiv id, from an arXiv DOI or an arxiv.org landing page."""
        candidates: list[str] = []
        doi = self.bare_doi or ""
        if doi.startswith("10.48550/arxiv."):
            candidates.append(doi.removeprefix("10.48550/arxiv."))
        for location in self.locations:
            match = _ARXIV_URL_RE.search(location.landing_page_url or "")
            if match:
                candidates.append(match["id"])
        for raw in candidates:
            arxiv_id = _strip_version(raw)
            if ARXIV_ID_RE.match(arxiv_id):
                return arxiv_id
        return None  # none found, or only malformed ones

    @property
    def subfield_ids(self) -> list[int]:
        out: list[int] = []
        for topic in self.topics:
            if topic.subfield:
                tail = topic.subfield.id.rsplit("/", 1)[-1]
                if tail.isdigit():
                    out.append(int(tail))
        return out

    def abstract(self) -> str | None:
        """Rebuild the abstract from OpenAlex's inverted word index."""
        index = self.abstract_inverted_index
        if not index:
            return None
        positions = sorted((pos, word) for word, spots in index.items() for pos in spots)
        text = " ".join(word for _, word in positions).strip()
        return text or None


_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(?P<id>[^?#\s]+?)(?:\.pdf)?$")
_VERSION_RE = re.compile(r"v\d+$")


_OLD_STYLE_RE = re.compile(r"^(?P<archive>[a-z-]+)(?:\.(?P<cls>[a-z]{2}))?/(?P<num>\d{7})$", re.I)


def _strip_version(arxiv_id: str) -> str:
    """Drop a version suffix, and restore case in old-style ids that came from a
    lowercased DOI (`math.co/0601001` -> `math.CO/0601001`)."""
    arxiv_id = _VERSION_RE.sub("", arxiv_id)
    match = _OLD_STYLE_RE.match(arxiv_id)
    if match:
        cls = f".{match['cls'].upper()}" if match["cls"] else ""
        return f"{match['archive'].lower()}{cls}/{match['num']}"
    return arxiv_id


def arxiv_doi(arxiv_id: str) -> str:
    """The DOI arXiv registers for every paper, which OpenAlex indexes."""
    return f"10.48550/arxiv.{arxiv_id}".lower()


@dataclass(slots=True)
class OAResult(SourceResult):
    works: dict[str, OAWork] = field(default_factory=dict)
    """Keyed by lowercase bare DOI."""
    requested: int = 0


class OpenAlexSource(Source):
    request_interval_s = REQUEST_INTERVAL_S
    api_key_env = "OPENALEX_API_KEY"

    def __init__(
        self,
        http: HttpClient,
        *,
        api_key: str | None = None,
        limiter: RateLimiter | None = None,
        per_call: int = DOIS_PER_CALL,
    ) -> None:
        super().__init__(http, limiter=limiter, api_key=api_key)
        self._per_call = per_call

    def by_dois(self, dois: Sequence[str]) -> OAResult:
        wanted = list(dict.fromkeys(d.lower() for d in dois))
        result = OAResult(requested=len(wanted))
        for start in range(0, len(wanted), self._per_call):
            chunk = wanted[start : start + self._per_call]
            params: dict[str, str | int] = {
                "filter": "doi:" + "|".join(chunk),
                "select": SELECT,
                "per_page": self._per_call,
            }
            if self._api_key:
                params["api_key"] = self._api_key
            try:
                response = self._get(WORKS_URL, params=params)
                rows = response.json().get("results", [])
            except Exception as exc:  # report and move on
                result.errors.append(f"dois at {start}: {type(exc).__name__}: {exc}")
                continue
            for row in rows:
                try:
                    work = OAWork.model_validate(row)
                except ValidationError as exc:
                    result.errors.append(f"{row.get('id')}: {exc.error_count()} validation errors")
                    continue
                if work.bare_doi:
                    result.works[work.bare_doi] = work
        return result

    def most_cited(
        self,
        subfield: int,
        *,
        since: dt.date,
        until: dt.date,
        min_citations: int,
        limit: int = WAVES_PER_QUERY,
    ) -> list[OAWork]:
        """The most-cited works with an abstract published in a window, for one subfield."""
        filters = ",".join(
            [
                f"primary_topic.subfield.id:{subfield}",
                f"from_publication_date:{since.isoformat()}",
                f"to_publication_date:{until.isoformat()}",
                f"cited_by_count:>{min_citations}",
                "has_abstract:true",
                "type:article|preprint",
            ]
        )
        params: dict[str, str | int] = {
            "filter": filters,
            "select": SELECT,
            "sort": "cited_by_count:desc",
            "per_page": min(limit, WAVES_PER_QUERY),
        }
        if self._api_key:
            params["api_key"] = self._api_key
        response = self._get(WORKS_URL, params=params)
        return [OAWork.model_validate(row) for row in response.json().get("results", [])]
