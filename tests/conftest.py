from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def paper_data() -> dict[str, Any]:
    """A minimal valid paper, as JSON-compatible data."""
    return {
        "id": "arxiv:2410.12345",
        "ids": {"arxiv": "2410.12345", "doi": "10.48550/arXiv.2410.12345"},
        "title": "A Test Paper on Graph Colouring",
        "authors": [{"name": "Ada Lovelace"}],
        "abstract": "We study vertex colourings of sparse graphs.",
        "topics": ["graph-theory"],
        "source_categories": ["math.CO"],
        "dates": {"first_seen": "2026-11-03", "submitted": "2026-10-29"},
        "links": {"abstract_page": "https://arxiv.org/abs/2410.12345"},
        "funnel": ["fresh"],
        "fetched_at": "2026-11-03T06:00:00Z",
    }
