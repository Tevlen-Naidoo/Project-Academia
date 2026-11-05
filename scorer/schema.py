"""Export the picks JSON Schema next to the fetcher's."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from fetcher.schema import SCHEMA_DIR, render
from scorer.models import PicksFile

SCHEMAS: dict[str, type[BaseModel]] = {"picks.schema.json": PicksFile}


def write_all(directory: Path = SCHEMA_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in SCHEMAS.items():
        path = directory / name
        path.write_text(render(model), encoding="utf-8")
        written.append(path)
    return written


def stale(directory: Path = SCHEMA_DIR) -> list[str]:
    return [
        name
        for name, model in SCHEMAS.items()
        if not (directory / name).exists()
        or (directory / name).read_text(encoding="utf-8") != render(model)
    ]
