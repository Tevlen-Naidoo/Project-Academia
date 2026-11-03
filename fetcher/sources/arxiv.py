"""arXiv API source: recent submissions in the configured categories.

API docs: https://info.arxiv.org/help/api/user-manual.html
Terms: one request every 3 seconds, one connection at a time.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from fetcher.http import HttpClient, RateLimiter

API_URL = "https://export.arxiv.org/api/query"
PAGE_SIZE = 500
MAX_RESULTS = 20_000
"""Safety cap; a 7-day window for the configured categories is well under this."""
REQUEST_INTERVAL_S = 3.0

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}
_ID_RE = re.compile(r"arxiv\.org/abs/(?P<id>.+?)(v(?P<version>\d+))?$")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ArxivAuthor:
    name: str
    affiliation: str | None = None


@dataclass(frozen=True, slots=True)
class ArxivEntry:
    arxiv_id: str
    version: int
    title: str
    abstract: str
    authors: tuple[ArxivAuthor, ...]
    primary_category: str
    categories: tuple[str, ...]
    submitted: dt.datetime
    updated: dt.datetime
    journal_ref: str | None
    doi: str | None
    comment: str | None


@dataclass(slots=True)
class ArxivResult:
    entries: list[ArxivEntry] = field(default_factory=list)
    total_reported: int = 0
    errors: list[str] = field(default_factory=list)


def _clean(text: str | None) -> str:
    return _SPACE_RE.sub(" ", text or "").strip()


def _parse_time(text: str | None) -> dt.datetime:
    if not text:
        raise ValueError("missing timestamp")
    return dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))


def category_term(category: str) -> str:
    """`cs.CV` -> `cat:cs.CV`; `astro-ph.*` -> `cat:astro-ph*` (arXiv's wildcard)."""
    if category.endswith(".*"):
        return f"cat:{category.removesuffix('.*')}*"
    return f"cat:{category}"


def build_query(categories: Sequence[str], start: dt.datetime, end: dt.datetime) -> str:
    if not categories:
        raise ValueError("no categories to query")
    cats = " OR ".join(category_term(c) for c in categories)
    fmt = "%Y%m%d%H%M"
    window = f"submittedDate:[{start.strftime(fmt)} TO {end.strftime(fmt)}]"
    return f"({cats}) AND {window}"


def parse_entry(entry: ET.Element) -> ArxivEntry:
    raw_id = _clean(entry.findtext("atom:id", namespaces=NS))
    match = _ID_RE.search(raw_id)
    if match is None:
        raise ValueError(f"unrecognised arXiv id {raw_id!r}")
    title = _clean(entry.findtext("atom:title", namespaces=NS))
    abstract = _clean(entry.findtext("atom:summary", namespaces=NS))
    if not title or not abstract:
        raise ValueError(f"{match['id']}: missing title or abstract")

    authors = tuple(
        ArxivAuthor(
            name=_clean(a.findtext("atom:name", namespaces=NS)),
            affiliation=_clean(a.findtext("arxiv:affiliation", namespaces=NS)) or None,
        )
        for a in entry.findall("atom:author", NS)
        if _clean(a.findtext("atom:name", namespaces=NS))
    )
    primary = entry.find("arxiv:primary_category", NS)
    categories = tuple(term for c in entry.findall("atom:category", NS) if (term := c.get("term")))
    primary_term = primary.get("term") if primary is not None else None
    primary_category = primary_term or (categories[0] if categories else "")
    if primary_category and primary_category not in categories:
        categories = (primary_category, *categories)

    return ArxivEntry(
        arxiv_id=match["id"],
        version=int(match["version"] or 1),
        title=title,
        abstract=abstract,
        authors=authors,
        primary_category=primary_category,
        categories=categories,
        submitted=_parse_time(entry.findtext("atom:published", namespaces=NS)),
        updated=_parse_time(entry.findtext("atom:updated", namespaces=NS)),
        journal_ref=_clean(entry.findtext("arxiv:journal_ref", namespaces=NS)) or None,
        doi=_clean(entry.findtext("arxiv:doi", namespaces=NS)) or None,
        comment=_clean(entry.findtext("arxiv:comment", namespaces=NS)) or None,
    )


def parse_feed(xml_bytes: bytes) -> tuple[list[ArxivEntry], int, list[str]]:
    """Entries, the total the API reports for the query, and per-entry errors."""
    root = ET.fromstring(xml_bytes)
    total = int(root.findtext("opensearch:totalResults", default="0", namespaces=NS))
    entries: list[ArxivEntry] = []
    errors: list[str] = []
    for node in root.findall("atom:entry", NS):
        # The API reports query errors as a single entry whose id is .../api/errors
        if "/api/errors" in (node.findtext("atom:id", default="", namespaces=NS)):
            raise ValueError(
                f"arXiv query error: {_clean(node.findtext('atom:summary', namespaces=NS))}"
            )
        try:
            entries.append(parse_entry(node))
        except ValueError as exc:
            errors.append(str(exc))
    return entries, total, errors


class ArxivSource:
    def __init__(
        self,
        http: HttpClient,
        *,
        limiter: RateLimiter | None = None,
        page_size: int = PAGE_SIZE,
        max_results: int = MAX_RESULTS,
    ) -> None:
        self._http = http
        self._limiter = limiter or RateLimiter(REQUEST_INTERVAL_S)
        self._page_size = page_size
        self._max_results = max_results

    def _pages(self, query: str) -> Iterator[tuple[list[ArxivEntry], int, list[str]]]:
        start = 0
        while start < self._max_results:
            response = self._http.get(
                API_URL,
                limiter=self._limiter,
                params={
                    "search_query": query,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "start": start,
                    "max_results": self._page_size,
                },
            )
            entries, total, errors = parse_feed(response.content)
            yield entries, total, errors
            start += self._page_size
            if not entries or start >= total:
                return

    def recent(
        self, categories: Sequence[str], *, since: dt.datetime, until: dt.datetime
    ) -> ArxivResult:
        """All submissions in `categories` between `since` and `until` (UTC)."""
        result = ArxivResult()
        seen: set[str] = set()
        for entries, total, errors in self._pages(build_query(categories, since, until)):
            result.total_reported = total
            result.errors.extend(errors)
            for entry in entries:
                if entry.arxiv_id not in seen:
                    seen.add(entry.arxiv_id)
                    result.entries.append(entry)
        if len(result.entries) < result.total_reported:
            result.errors.append(
                f"got {len(result.entries)} of {result.total_reported} reported results"
            )
        return result
