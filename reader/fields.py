"""Fields of study: keep Background papers in the pick's own field.

The first live run picked Background papers by citation count alone, so a CS
paper applied to biology got DESeq2 and a medical review as "background". A
reference now needs a field the pick belongs to (from S2 `fieldsOfStudy` or the
OpenAlex primary topic), or a direct signal from S2 that the pick builds on it.

Field names follow Semantic Scholar's vocabulary ("Computer Science", ...).
"""

from __future__ import annotations

from fetcher.models import Paper
from fetcher.topics import TopicsConfig

CS = frozenset({"Computer Science", "Engineering"})
HUMANITIES = frozenset({"History", "Art", "Philosophy"})
SOCIAL = frozenset({"Sociology", "Political Science", "Education", "Law", "Geography"})

ARXIV_PREFIX_FIELDS: dict[str, frozenset[str]] = {
    "cs": CS,
    "eess": CS,
    "stat": CS,
    "math": frozenset({"Mathematics"}),
    "astro-ph": frozenset({"Physics"}),
    "physics.hist-ph": HUMANITIES,
    "physics": frozenset({"Physics"}),
    "q-bio": frozenset({"Biology"}),
}

OPENALEX_FIELD_CODES: dict[int, frozenset[str]] = {
    12: HUMANITIES,
    17: CS,
    22: CS,
    26: frozenset({"Mathematics"}),
    31: frozenset({"Physics"}),
    33: SOCIAL,
}
"""OpenAlex subfield ids start with their field's code: 1702 is in field 17."""

OPENALEX_FIELD_NAMES: dict[str, frozenset[str]] = {
    "Computer Science": CS,
    "Engineering": CS,
    "Mathematics": frozenset({"Mathematics"}),
    "Physics and Astronomy": frozenset({"Physics"}),
    "Arts and Humanities": HUMANITIES,
    "Social Sciences": SOCIAL,
    "Medicine": frozenset({"Medicine"}),
    "Biochemistry, Genetics and Molecular Biology": frozenset({"Biology"}),
    "Neuroscience": frozenset({"Biology", "Psychology"}),
    "Psychology": frozenset({"Psychology"}),
    "Economics, Econometrics and Finance": frozenset({"Economics"}),
}


def _arxiv_fields(category: str) -> frozenset[str]:
    for prefix in sorted(ARXIV_PREFIX_FIELDS, key=len, reverse=True):
        if category == prefix or category.startswith(prefix + "."):
            return ARXIV_PREFIX_FIELDS[prefix]
    return frozenset()


def openalex_fields(field_name: str | None) -> frozenset[str]:
    if not field_name:
        return frozenset()
    return OPENALEX_FIELD_NAMES.get(field_name, frozenset({field_name}))


def pick_fields(pick: Paper, topics: TopicsConfig) -> frozenset[str]:
    """The fields a pick belongs to, from its topics' arXiv categories and OpenAlex
    subfields. Empty means unknown: no field filter is applied."""
    out: set[str] = set()
    for tag in pick.topics:
        topic = topics.topics.get(tag)
        if topic is None:
            continue
        for rule in topic.arxiv:
            out |= _arxiv_fields(rule.category)
        for oa in topic.openalex:
            out |= OPENALEX_FIELD_CODES.get(oa.subfield // 100, frozenset())
    for category in pick.source_categories:
        out |= _arxiv_fields(category)
    return frozenset(out)
