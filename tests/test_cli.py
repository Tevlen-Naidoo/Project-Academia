from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fetcher import schema
from fetcher.cli import main


def test_committed_schemas_are_up_to_date() -> None:
    assert schema.stale() == [], "run `python -m fetcher schema` and commit the result"


def test_topics_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["topics"]) == 0
    assert "graph-theory" in capsys.readouterr().out


def test_validate_command(tmp_path: Path, paper_data: dict[str, Any]) -> None:
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {"date": "2026-11-03", "generated_at": "2026-11-03T06:05:00Z", "papers": [paper_data]}
        )
    )
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"date": "2026-11-03"}))
    assert main(["validate", str(good)]) == 0
    assert main(["validate", str(good), str(bad)]) == 1
