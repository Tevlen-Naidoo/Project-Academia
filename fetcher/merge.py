"""De-duplicate candidates from different funnels into one record per paper."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from fetcher.enrich import EVIDENCE_RANK
from fetcher.models import Paper

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return _NON_WORD.sub(" ", text).strip()


def _keys(paper: Paper) -> list[str]:
    keys = []
    if paper.ids.arxiv:
        keys.append(f"arxiv:{paper.ids.arxiv}")
    if paper.ids.doi:
        keys.append(f"doi:{paper.ids.doi.lower()}")
    if paper.ids.openalex:
        keys.append(f"openalex:{paper.ids.openalex}")
    title = normalize_title(paper.title)
    first_author = paper.authors[0].name.split()[-1].lower() if paper.authors else ""
    if len(title) >= 20:
        keys.append(f"title:{title}|{first_author}")
    return keys


def _absorb(kept: Paper, other: Paper) -> None:
    """Fill what `kept` lacks from `other`, and record both funnels."""
    for field in ("arxiv", "doi", "s2", "openalex"):
        if getattr(kept.ids, field) is None and getattr(other.ids, field) is not None:
            setattr(kept.ids, field, getattr(other.ids, field))
    kept.id = kept.ids.canonical()
    kept.funnel = list(dict.fromkeys([*kept.funnel, *other.funnel]))
    kept.topics = list(dict.fromkeys([*kept.topics, *other.topics]))
    if not kept.citations.by_year and other.citations.by_year:
        kept.citations = other.citations
    if kept.dates.published is None:
        kept.dates.published = other.dates.published
    if kept.dates.submitted is None:
        kept.dates.submitted = other.dates.submitted
    if EVIDENCE_RANK[other.publication.evidence] > EVIDENCE_RANK[kept.publication.evidence]:
        kept.publication = other.publication


def merge(*groups: Iterable[Paper]) -> list[Paper]:
    """One record per paper, earlier groups winning ties. Order is preserved."""
    merged: list[Paper] = []
    index: dict[str, Paper] = {}
    for group in groups:
        for paper in group:
            keys = _keys(paper)
            existing = next((index[k] for k in keys if k in index), None)
            if existing is None:
                merged.append(paper)
                existing = paper
            else:
                _absorb(existing, paper)
            for key in _keys(existing):
                index[key] = existing
    return merged
