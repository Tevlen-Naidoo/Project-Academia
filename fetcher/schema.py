"""Export the JSON Schemas that steps 2 and 3 read."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from fetcher.models import CandidatesFile, Paper

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
SCHEMAS: dict[str, type[BaseModel]] = {
    "paper.schema.json": Paper,
    "candidates.schema.json": CandidatesFile,
}


def render(model: type[BaseModel]) -> str:
    schema: dict[str, Any] = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write_all(directory: Path = SCHEMA_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in SCHEMAS.items():
        path = directory / name
        path.write_text(render(model), encoding="utf-8")
        written.append(path)
    return written


def stale(directory: Path = SCHEMA_DIR) -> list[str]:
    """Names of schema files that are missing or differ from the models."""
    out: list[str] = []
    for name, model in SCHEMAS.items():
        path = directory / name
        if not path.exists() or path.read_text(encoding="utf-8") != render(model):
            out.append(name)
    return out
