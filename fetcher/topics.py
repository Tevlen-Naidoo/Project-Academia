"""Load config/topics.yaml and map source categories to internal topic tags."""

from __future__ import annotations

import re
from collections.abc import Iterable
from fnmatch import fnmatchcase
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from fetcher.models import TopicTag

DEFAULT_TOPICS_PATH = Path(__file__).resolve().parent.parent / "config" / "topics.yaml"


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _keyword_pattern(keywords: Iterable[str]) -> re.Pattern[str] | None:
    words = [re.escape(k) for k in keywords]
    if not words:
        return None
    return re.compile(r"(?<![\w-])(" + "|".join(words) + r")(?![\w-])", re.IGNORECASE)


class ArxivRule(_Config):
    category: Annotated[str, Field(min_length=1)]
    """An arXiv category such as `cs.CV`, or a wildcard such as `astro-ph.*`."""
    keywords: tuple[str, ...] = ()

    def matches_category(self, category: str) -> bool:
        if self.category.endswith(".*"):
            archive = self.category.removesuffix(".*")
            return category == archive or fnmatchcase(category, self.category)
        return category == self.category


class OpenAlexRule(_Config):
    subfield: Annotated[int, Field(ge=1000, le=9999)]
    keywords: tuple[str, ...] = ()


class Topic(_Config):
    label: Annotated[str, Field(min_length=1)]
    weight: Literal["core", "light"]
    arxiv: tuple[ArxivRule, ...] = ()
    openalex: tuple[OpenAlexRule, ...] = ()
    citation_scale: Annotated[float, Field(gt=0, le=1)] = 1.0
    """Multiplies the making-waves citation minimums, for fields that cite slowly."""

    @field_validator("arxiv", mode="before")
    @classmethod
    def _arxiv_shorthand(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"category": v} if isinstance(v, str) else v for v in value]
        return value

    @field_validator("openalex", mode="before")
    @classmethod
    def _openalex_shorthand(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"subfield": v} if isinstance(v, int) else v for v in value]
        return value


class TopicsConfig(_Config):
    model_config = ConfigDict(extra="forbid", frozen=True, ignored_types=(cached_property,))

    version: Literal[1]
    topics: dict[TopicTag, Topic]

    @cached_property
    def _patterns(self) -> dict[tuple[str, ...], re.Pattern[str] | None]:
        cache: dict[tuple[str, ...], re.Pattern[str] | None] = {}
        for topic in self.topics.values():
            keyword_sets = [r.keywords for r in topic.arxiv] + [r.keywords for r in topic.openalex]
            for keywords in keyword_sets:
                cache.setdefault(keywords, _keyword_pattern(keywords))
        return cache

    def _keywords_hit(self, keywords: tuple[str, ...], text: str) -> bool:
        pattern = self._patterns.get(keywords)
        return pattern is None or pattern.search(text) is not None

    def arxiv_query_categories(self) -> list[str]:
        """Every arXiv category or wildcard to query, in config order, without duplicates."""
        seen: dict[str, None] = {}
        for topic in self.topics.values():
            for rule in topic.arxiv:
                seen.setdefault(rule.category, None)
        return list(seen)

    def openalex_subfields(self) -> list[int]:
        seen: dict[int, None] = {}
        for topic in self.topics.values():
            for rule in topic.openalex:
                seen.setdefault(rule.subfield, None)
        return list(seen)

    def citation_scale(self, subfield: int) -> float:
        """The lowest `citation_scale` of any topic that uses this OpenAlex subfield."""
        scales = [
            topic.citation_scale
            for topic in self.topics.values()
            if any(rule.subfield == subfield for rule in topic.openalex)
        ]
        return min(scales, default=1.0)

    def tag(
        self,
        *,
        arxiv_categories: Iterable[str] = (),
        openalex_subfields: Iterable[int] = (),
        title: str = "",
        abstract: str = "",
    ) -> list[str]:
        """The topic tags for a paper, in config order."""
        categories = list(arxiv_categories)
        subfields = set(openalex_subfields)
        text = f"{title}\n{abstract}"
        tags: list[str] = []
        for tag, topic in self.topics.items():
            arxiv_hit = any(
                rule.matches_category(c) and self._keywords_hit(rule.keywords, text)
                for rule in topic.arxiv
                for c in categories
            )
            openalex_hit = any(
                rule.subfield in subfields and self._keywords_hit(rule.keywords, text)
                for rule in topic.openalex
            )
            if arxiv_hit or openalex_hit:
                tags.append(tag)
        return tags


def load_topics(path: Path = DEFAULT_TOPICS_PATH) -> TopicsConfig:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return TopicsConfig.model_validate(raw)
