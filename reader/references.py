"""Reference lists from Semantic Scholar, with OpenAlex as the fallback.

S2 docs: https://api.semanticscholar.org/api-docs/graph (paper references endpoint).
Each S2 reference says whether it is influential for the citing paper and why it
was cited (`background`, `methodology`, `result`). OpenAlex gives only the list of
cited works, so references from it carry no influence flag or intents.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fetcher.http import HttpClient, RateLimiter
from fetcher.models import ARXIV_ID_RE, Links, PaperIds
from fetcher.sources.openalex import WORKS_URL
from fetcher.sources.semantic_scholar import REQUEST_INTERVAL_S as S2_INTERVAL_S
from reader.fields import openalex_fields
from reader.models import PaperRef

S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/{id}/references"
S2_REFERENCE_FIELDS = ",".join(
    [
        "isInfluential",
        "intents",
        "contexts",
        "paperId",
        "externalIds",
        "title",
        "year",
        "venue",
        "publicationTypes",
        "citationCount",
        "influentialCitationCount",
        "fieldsOfStudy",
        "s2FieldsOfStudy",
    ]
)
S2_PAGE_LIMIT = 1000
OA_INTERVAL_S = 0.2
OA_IDS_PER_CALL = 100
OA_SELECT = (
    "id,doi,display_name,publication_year,cited_by_count,type,primary_location,primary_topic"
)
REVIEW_TYPES = frozenset({"Review"})
_DOI_RE = re.compile(r"^10\.\S+/\S+$")
_ARXIV_DOI_PREFIX = "10.48550/arxiv."


@dataclass(slots=True)
class Reference:
    """One paper in a reference list, with what the citing paper says about it."""

    paper: PaperRef
    influential: bool | None = None
    """None when the source cannot tell (OpenAlex)."""
    intents: list[str] | None = None
    contexts: list[str] = field(default_factory=list)
    fields: frozenset[str] = frozenset()
    """Fields of study of the cited paper (S2 vocabulary); empty when unknown."""

    @property
    def has_signals(self) -> bool:
        return self.influential is not None


@dataclass(slots=True)
class ReferenceList:
    references: list[Reference] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class S2Field(_Lenient):
    category: str | None = None


class S2CitedPaper(_Lenient):
    paper_id: str | None = Field(default=None, alias="paperId")
    external_ids: dict[str, str | int] | None = Field(default=None, alias="externalIds")
    title: str | None = None
    year: int | None = None
    venue: str | None = None
    publication_types: list[str] | None = Field(default=None, alias="publicationTypes")
    citation_count: int | None = Field(default=None, alias="citationCount")
    influential_citation_count: int | None = Field(default=None, alias="influentialCitationCount")
    fields_of_study: list[str] | None = Field(default=None, alias="fieldsOfStudy")
    s2_fields_of_study: list[S2Field] | None = Field(default=None, alias="s2FieldsOfStudy")

    def fields(self) -> frozenset[str]:
        out = set(self.fields_of_study or ())
        out.update(f.category for f in self.s2_fields_of_study or () if f.category)
        return frozenset(out)


class S2Reference(_Lenient):
    is_influential: bool = Field(default=False, alias="isInfluential")
    intents: list[str] | None = None
    contexts: list[str] | None = None
    cited_paper: S2CitedPaper = Field(alias="citedPaper")


class OAPrimarySource(_Lenient):
    display_name: str | None = None


class OAPrimaryLocation(_Lenient):
    source: OAPrimarySource | None = None


class OAField(_Lenient):
    display_name: str | None = None


class OATopicField(_Lenient):
    field: OAField | None = None


class OARefWork(_Lenient):
    id: str
    doi: str | None = None
    display_name: str | None = None
    publication_year: int | None = None
    cited_by_count: int | None = None
    type: str | None = None
    primary_location: OAPrimaryLocation | None = None
    primary_topic: OATopicField | None = None

    def fields(self) -> frozenset[str]:
        topic = self.primary_topic
        return openalex_fields(topic.field.display_name if topic and topic.field else None)


def _clean_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.lower().removeprefix("https://doi.org/")
    return doi if _DOI_RE.match(doi) else None


def _clean_arxiv(arxiv: str | None) -> str | None:
    if not arxiv:
        return None
    arxiv = re.sub(r"v\d+$", "", arxiv)
    return arxiv if ARXIV_ID_RE.match(arxiv) else None


def _links(ids: PaperIds) -> Links:
    abstract_page: str | None = None
    if ids.arxiv:
        abstract_page = f"https://arxiv.org/abs/{ids.arxiv}"
    elif ids.s2:
        abstract_page = f"https://www.semanticscholar.org/paper/{ids.s2}"
    elif ids.openalex:
        abstract_page = f"https://openalex.org/{ids.openalex}"
    data: dict[str, Any] = {"abstract_page": abstract_page}
    if ids.arxiv:
        data["pdf"] = f"https://arxiv.org/pdf/{ids.arxiv}"
    if ids.doi:
        data["doi"] = f"https://doi.org/{ids.doi}"
    return Links.model_validate(data)


def ref_from_s2(cited: S2CitedPaper) -> PaperRef | None:
    """A PaperRef for an S2 cited paper, or None when S2 could not resolve it."""
    if not cited.paper_id or not cited.title:
        return None
    ext = cited.external_ids or {}
    arxiv = _clean_arxiv(str(ext["ArXiv"])) if ext.get("ArXiv") else None
    doi = _clean_doi(str(ext["DOI"])) if ext.get("DOI") else None
    ids = PaperIds(arxiv=arxiv, doi=doi, s2=cited.paper_id)
    return PaperRef(
        id=ids.canonical(),
        ids=ids,
        title=cited.title.strip(),
        year=cited.year,
        venue=cited.venue or None,
        citations=cited.citation_count,
        influential_citations=cited.influential_citation_count,
        is_review=bool(set(cited.publication_types or ()) & REVIEW_TYPES),
        links=_links(ids),
    )


def ref_from_openalex(work: OARefWork) -> PaperRef | None:
    if not work.display_name:
        return None
    doi = _clean_doi(work.doi)
    arxiv = None
    if doi and doi.startswith(_ARXIV_DOI_PREFIX):
        arxiv = _clean_arxiv(doi.removeprefix(_ARXIV_DOI_PREFIX))
    ids = PaperIds(arxiv=arxiv, doi=doi, openalex=work.id.rsplit("/", 1)[-1])
    venue = None
    if work.primary_location and work.primary_location.source:
        venue = work.primary_location.source.display_name
    return PaperRef(
        id=ids.canonical(),
        ids=ids,
        title=work.display_name.strip(),
        year=work.publication_year,
        venue=venue,
        citations=work.cited_by_count,
        is_review=work.type == "review",
        links=_links(ids),
    )


def s2_key(ids: PaperIds) -> str | None:
    """The id to ask S2 with: its own id, then arXiv, then DOI."""
    if ids.s2:
        return ids.s2
    if ids.arxiv:
        return f"ARXIV:{ids.arxiv}"
    if ids.doi:
        return f"DOI:{ids.doi}"
    return None


def openalex_key(ids: PaperIds) -> str | None:
    if ids.openalex:
        return ids.openalex
    doi = ids.doi or (f"10.48550/arXiv.{ids.arxiv}" if ids.arxiv else None)
    return f"doi:{doi}" if doi else None


class ReferenceClient:
    """Fetches reference lists: S2 first, OpenAlex when S2 has none."""

    def __init__(
        self,
        http: HttpClient,
        *,
        s2_api_key: str | None = None,
        openalex_api_key: str | None = None,
        s2_limiter: RateLimiter | None = None,
        oa_limiter: RateLimiter | None = None,
    ) -> None:
        self._http = http
        self._s2_key = s2_api_key if s2_api_key is not None else os.environ.get("S2_API_KEY")
        self._oa_key = (
            openalex_api_key if openalex_api_key is not None else os.environ.get("OPENALEX_API_KEY")
        )
        self._s2_limiter = s2_limiter or RateLimiter(S2_INTERVAL_S)
        self._oa_limiter = oa_limiter or RateLimiter(OA_INTERVAL_S)
        self.calls = 0

    def references(self, ids: PaperIds) -> tuple[str, ReferenceList]:
        """The reference list and where it came from: "s2", "openalex" or "none"."""
        result = ReferenceList()
        key = s2_key(ids)
        if key:
            s2 = self.s2_references(key)
            result.errors.extend(s2.errors)
            if s2.references:
                return "s2", ReferenceList(s2.references, result.errors)
        key = openalex_key(ids)
        if key:
            oa = self.openalex_references(key)
            result.errors.extend(oa.errors)
            if oa.references:
                return "openalex", ReferenceList(oa.references, result.errors)
        return "none", result

    def s2_references(self, key: str) -> ReferenceList:
        result = ReferenceList()
        headers = {"x-api-key": self._s2_key} if self._s2_key else None
        try:
            self.calls += 1
            response = self._http.get(
                S2_PAPER_URL.format(id=key),
                limiter=self._s2_limiter,
                params={"fields": S2_REFERENCE_FIELDS, "limit": S2_PAGE_LIMIT},
                headers=headers,
            )
            rows = response.json().get("data") or []
        except Exception as exc:  # 404 for unknown papers, network, bad JSON: report and move on
            result.errors.append(f"S2 references for {key}: {type(exc).__name__}: {exc}")
            return result
        for row in rows:
            try:
                item = S2Reference.model_validate(row)
            except ValidationError as exc:
                result.errors.append(f"S2 reference row: {exc.error_count()} validation errors")
                continue
            paper = ref_from_s2(item.cited_paper)
            if paper is None:
                continue
            result.references.append(
                Reference(
                    paper=paper,
                    influential=item.is_influential,
                    intents=item.intents or [],
                    contexts=item.contexts or [],
                    fields=item.cited_paper.fields(),
                )
            )
        return result

    def openalex_references(self, key: str) -> ReferenceList:
        result = ReferenceList()
        params: dict[str, str | int] = {"select": "id,referenced_works"}
        if self._oa_key:
            params["api_key"] = self._oa_key
        try:
            self.calls += 1
            response = self._http.get(f"{WORKS_URL}/{key}", limiter=self._oa_limiter, params=params)
            cited = [str(w).rsplit("/", 1)[-1] for w in response.json().get("referenced_works", [])]
        except Exception as exc:
            result.errors.append(f"OpenAlex references for {key}: {type(exc).__name__}: {exc}")
            return result
        for start in range(0, len(cited), OA_IDS_PER_CALL):
            chunk = cited[start : start + OA_IDS_PER_CALL]
            params = {
                "filter": "openalex:" + "|".join(chunk),
                "select": OA_SELECT,
                "per_page": OA_IDS_PER_CALL,
            }
            if self._oa_key:
                params["api_key"] = self._oa_key
            try:
                self.calls += 1
                response = self._http.get(WORKS_URL, limiter=self._oa_limiter, params=params)
                rows = response.json().get("results", [])
            except Exception as exc:
                result.errors.append(f"OpenAlex works at {start}: {type(exc).__name__}: {exc}")
                continue
            for row in rows:
                try:
                    work = OARefWork.model_validate(row)
                    paper = ref_from_openalex(work)
                except ValidationError as exc:
                    result.errors.append(f"OpenAlex work: {exc.error_count()} validation errors")
                    continue
                if paper is not None:
                    result.references.append(Reference(paper=paper, fields=work.fields()))
        return result
