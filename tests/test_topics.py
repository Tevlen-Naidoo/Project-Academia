from __future__ import annotations

import pytest

from fetcher.topics import TopicsConfig, load_topics


@pytest.fixture(scope="module")
def config() -> TopicsConfig:
    return load_topics()


def test_all_planned_topics_present(config: TopicsConfig) -> None:
    assert set(config.topics) == {
        "ml", "ai", "vision", "image-processing", "nlp",
        "graph-theory", "robotics", "theory", "astronomy",
    }  # fmt: skip
    assert config.topics["astronomy"].weight == "light"


def test_cross_listed_paper_gets_both_tags(config: TopicsConfig) -> None:
    assert config.tag(arxiv_categories=["cs.LG", "cs.RO"]) == ["ml", "robotics"]


def test_astro_wildcard(config: TopicsConfig) -> None:
    assert config.tag(arxiv_categories=["astro-ph.GA"]) == ["astronomy"]
    assert config.tag(arxiv_categories=["astro-ph"]) == ["astronomy"]
    assert config.tag(arxiv_categories=["astro-physics"]) == []


def test_math_co_needs_a_graph_keyword(config: TopicsConfig) -> None:
    no_graph = config.tag(arxiv_categories=["math.CO"], title="Partitions of integers")
    assert no_graph == []
    graph = config.tag(arxiv_categories=["math.CO"], abstract="Every planar graph is 4-colourable.")
    assert graph == ["graph-theory"]


def test_keywords_match_whole_words(config: TopicsConfig) -> None:
    # "edgeworth" must not count as "edge"
    assert config.tag(arxiv_categories=["math.CO"], title="Edgeworth expansions") == []


def test_openalex_keyword_rules(config: TopicsConfig) -> None:
    tags = config.tag(openalex_subfields=[1702], title="Large language models as reasoners")
    assert tags == ["ml", "ai", "nlp"]
    assert config.tag(openalex_subfields=[1702], title="Planning with heuristics") == ["ai"]


def test_unmapped_category_gets_no_tag(config: TopicsConfig) -> None:
    assert config.tag(arxiv_categories=["q-bio.NC"]) == []


def test_query_lists(config: TopicsConfig) -> None:
    cats = config.arxiv_query_categories()
    assert cats[:2] == ["cs.LG", "stat.ML"]
    assert "astro-ph.*" in cats
    assert len(cats) == len(set(cats))
    assert 1702 in config.openalex_subfields()


def test_shorthand_and_object_forms_are_equivalent() -> None:
    raw = {
        "version": 1,
        "topics": {
            "a": {"label": "A", "weight": "core", "arxiv": ["cs.AI"], "openalex": [1702]},
            "b": {
                "label": "B",
                "weight": "core",
                "arxiv": [{"category": "cs.AI"}],
                "openalex": [{"subfield": 1702}],
            },
        },
    }
    config = TopicsConfig.model_validate(raw)
    assert config.topics["a"].arxiv == config.topics["b"].arxiv
    assert config.topics["a"].openalex == config.topics["b"].openalex
