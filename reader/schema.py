"""Export the prerequisites and reading board JSON Schemas next to the fetcher's."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from fetcher.schema import SCHEMA_DIR, render
from reader.board import Board
from reader.models import PrereqsFile

SCHEMAS: dict[str, type[BaseModel]] = {
    "prereqs.schema.json": PrereqsFile,
    "board.schema.json": Board,
}


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
