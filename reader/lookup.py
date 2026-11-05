"""Turn what the user types (an arXiv id, a DOI, a link) into a `PaperRef`.

Papers the pipeline already saved (candidates and prerequisites in data/) are
found locally; anything else is looked up on Semantic Scholar.
S2 docs: https://api.semanticscholar.org/api-docs/graph (paper details endpoint).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

from pydantic import Field, ValidationError

from fetcher.http import HttpClient, RateLimiter
from fetcher.models import CandidatesFile, Paper, PaperIds
from fetcher.sources.semantic_scholar import REQUEST_INTERVAL_S as S2_INTERVAL_S
from reader.board import BoardError
from reader.models import PaperRef, PrereqsFile
from reader.references import (
    S2_REFERENCE_FIELDS,
    S2CitedPaper,
    _clean_arxiv,
    _clean_doi,
    ref_from_s2,
    s2_key,
)

S2_DETAILS_URL = "https://api.semanticscholar.org/graph/v1/paper/{id}"
S2_DETAILS_FIELDS = ",".join(
    [f for f in S2_REFERENCE_FIELDS.split(",") if f not in {"isInfluential", "intents", "contexts"}]
    + ["tldr"]
)

_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([^\s?#]+?)(?:\.pdf)?/?(?:[?#].*)?$", re.I)
_DOI_URL = re.compile(r"(?:dx\.)?doi\.org/(10\.\S+)$", re.I)
_S2_HEX = re.compile(r"^[0-9a-f]{40}$")
_S2_URL = re.compile(r"semanticscholar\.org/paper/(?:[^/]+/)?([0-9a-f]{40})", re.I)
_OPENALEX = re.compile(r"^(?:https?://openalex\.org/)?(W\d+)$", re.I)


class _S2Details(S2CitedPaper):
    tldr: dict[str, str | None] | None = Field(default=None)


def parse_ids(text: str) -> PaperIds:
    """The ids named by `text`: an id, a canonical id like `arxiv:2410.12345`, or a link."""
    raw = text.strip()
    if not raw:
        raise BoardError("give an arXiv id, a DOI or a link to the paper")
    prefix, _, rest = raw.partition(":")
    prefix = prefix.lower()
    if prefix == "arxiv" and (arxiv := _clean_arxiv(rest.strip())):
        return PaperIds(arxiv=arxiv)
    if prefix == "doi" and (doi := _clean_doi(rest.strip())):
        return PaperIds(doi=doi)
    if prefix == "s2" and _S2_HEX.match(rest.strip().lower()):
        return PaperIds(s2=rest.strip().lower())
    if prefix == "openalex" and (m := _OPENALEX.match(rest.strip())):
        return PaperIds(openalex=m.group(1).upper())
    if (m := _ARXIV_URL.search(raw)) and (arxiv := _clean_arxiv(unquote(m.group(1)))):
        return PaperIds(arxiv=arxiv)
    if (m := _DOI_URL.search(raw)) and (doi := _clean_doi(unquote(m.group(1)))):
        return PaperIds(doi=doi)
    if m := _S2_URL.search(raw):
        return PaperIds(s2=m.group(1).lower())
    if m := _OPENALEX.match(raw):
        return PaperIds(openalex=m.group(1).upper())
    if _S2_HEX.match(raw.lower()):
        return PaperIds(s2=raw.lower())
    if arxiv := _clean_arxiv(raw):
        return PaperIds(arxiv=arxiv)
    if doi := _clean_doi(raw):
        return PaperIds(doi=doi)
    raise BoardError(f"can't tell which paper {raw!r} is: use an arXiv id, a DOI or a link")


def _same(a: PaperIds, b: PaperIds) -> bool:
    pairs = ((a.arxiv, b.arxiv), (a.doi, b.doi), (a.s2, b.s2), (a.openalex, b.openalex))
    return any(x is not None and y is not None and x.lower() == y.lower() for x, y in pairs)


def ref_from_paper(paper: Paper) -> PaperRef:
    return PaperRef(
        id=paper.id,
        ids=paper.ids,
        title=paper.title,
        year=(paper.dates.published or paper.dates.submitted or paper.dates.first_seen).year,
        venue=paper.publication.venue,
        citations=paper.citations.total,
        influential_citations=paper.citations.influential,
        tldr=paper.tldr,
        links=paper.links,
    )


def _newest_first(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json"), reverse=True) if directory.is_dir() else []


def _local_refs(data_dir: Path) -> Iterator[PaperRef]:
    for path in _newest_first(data_dir / "prereqs"):
        try:
            prereqs = PrereqsFile.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            continue
        for pick in prereqs.picks:
            for pre in pick.prerequisites:
                yield pre.paper
    for directory in ("picks", "candidates"):
        for path in _newest_first(data_dir / directory):
            try:
                candidates = CandidatesFile.model_validate_json(path.read_bytes())
            except (OSError, ValidationError):
                continue
            for paper in candidates.papers:
                yield ref_from_paper(paper)


def find_local(ids: PaperIds, data_dir: Path) -> PaperRef | None:
    """The newest saved record of this paper in data/, if the pipeline has seen it."""
    return next((ref for ref in _local_refs(data_dir) if _same(ref.ids, ids)), None)


class PaperLookup:
    def __init__(
        self,
        http: HttpClient,
        *,
        data_dir: Path = Path("data"),
        s2_api_key: str | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._http = http
        self._data_dir = data_dir
        self._key = s2_api_key if s2_api_key is not None else os.environ.get("S2_API_KEY")
        self._limiter = limiter or RateLimiter(S2_INTERVAL_S)

    def resolve(self, ids: PaperIds) -> PaperRef:
        local = find_local(ids, self._data_dir)
        if local is not None:
            return local
        return self.from_s2(ids)

    def from_s2(self, ids: PaperIds) -> PaperRef:
        key = s2_key(ids)
        if key is None:
            raise BoardError(f"{ids.canonical()} is not in data/; add it by arXiv id or DOI")
        headers = {"x-api-key": self._key} if self._key else None
        try:
            response = self._http.get(
                S2_DETAILS_URL.format(id=key),
                limiter=self._limiter,
                params={"fields": S2_DETAILS_FIELDS},
                headers=headers,
            )
            details = _S2Details.model_validate(response.json())
        except Exception as exc:  # 404, network, bad JSON: one clear message for the user
            raise BoardError(
                f"couldn't look up {ids.canonical()} on Semantic Scholar "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        ref = ref_from_s2(details)
        if ref is None:
            raise BoardError(f"Semantic Scholar has no title for {ids.canonical()}")
        tldr = (details.tldr or {}).get("text")
        return ref.model_copy(update={"tldr": tldr}) if tldr else ref
