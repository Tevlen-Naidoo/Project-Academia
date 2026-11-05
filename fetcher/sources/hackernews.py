"""Hacker News, via the Algolia search API: stories that link to a paper.

Endpoint: https://hn.algolia.com/api/v1/search_by_date
Public and unauthenticated. We search story URLs for arxiv.org and doi.org.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError

from fetcher.http import HttpClient, RateLimiter

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HITS_PER_PAGE = 1000
MAX_PAGES = 5
REQUEST_INTERVAL_S = 1.0
QUERIES = ("arxiv.org", "doi.org")

_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(?P<id>\d{4}\.\d{4,5}|[a-z-]+/\d{7})")
_DOI_RE = re.compile(r"doi\.org/(?P<doi>10\.\d{4,9}/[^\s?#]+)", re.IGNORECASE)


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class HNHit(_Lenient):
    url: str | None = None
    points: int | None = None
    num_comments: int | None = None


@dataclass(frozen=True, slots=True)
class HNBuzz:
    points: int
    comments: int


@dataclass(slots=True)
class HNResult:
    by_arxiv: dict[str, HNBuzz] = field(default_factory=dict)
    by_doi: dict[str, HNBuzz] = field(default_factory=dict)
    """Keyed by lowercase DOI."""
    stories: int = 0
    errors: list[str] = field(default_factory=list)


def paper_key(url: str) -> tuple[str, str] | None:
    """`("arxiv", id)` or `("doi", doi)` for a story URL that points at a paper."""
    match = _ARXIV_RE.search(url)
    if match:
        return "arxiv", match["id"]
    match = _DOI_RE.search(url)
    if match:
        return "doi", match["doi"].rstrip(".").lower()
    return None


def _add(table: dict[str, HNBuzz], key: str, hit: HNHit) -> None:
    # Several stories about one paper: sum them, since each is separate attention
    old = table.get(key, HNBuzz(0, 0))
    table[key] = HNBuzz(old.points + (hit.points or 0), old.comments + (hit.num_comments or 0))


class HackerNewsSource:
    def __init__(self, http: HttpClient, *, limiter: RateLimiter | None = None) -> None:
        self._http = http
        self._limiter = limiter or RateLimiter(REQUEST_INTERVAL_S)

    def recent(self, *, since: dt.datetime) -> HNResult:
        result = HNResult()
        for query in QUERIES:
            for page in range(MAX_PAGES):
                try:
                    response = self._http.get(
                        SEARCH_URL,
                        limiter=self._limiter,
                        params={
                            "query": query,
                            "tags": "story",
                            "restrictSearchableAttributes": "url",
                            "numericFilters": f"created_at_i>{int(since.timestamp())}",
                            "hitsPerPage": HITS_PER_PAGE,
                            "page": page,
                        },
                    )
                    body = response.json()
                except Exception as exc:
                    result.errors.append(f"{query} page {page}: {type(exc).__name__}: {exc}")
                    break
                for row in body.get("hits", []):
                    try:
                        hit = HNHit.model_validate(row)
                    except ValidationError:
                        continue
                    key = paper_key(hit.url or "")
                    if key is None:
                        continue
                    result.stories += 1
                    kind, value = key
                    _add(result.by_arxiv if kind == "arxiv" else result.by_doi, value, hit)
                if page + 1 >= int(body.get("nbPages", 0)):
                    break
        return result
