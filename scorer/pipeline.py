"""Read a candidates file, score it, and write the day's picks file."""

from __future__ import annotations

import datetime as dt
from collections.abc import Set as AbstractSet
from pathlib import Path

from fetcher.models import CandidatesFile
from fetcher.pipeline import DATA_DIR
from fetcher.topics import TopicsConfig
from scorer.config import ScoringConfig
from scorer.models import PicksFile
from scorer.select import PICKS_SUBDIR, choose, load_history
from scorer.signals import score_all


def candidates_path(today: dt.date, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "candidates" / f"{today.isoformat()}.json"


def run(
    candidates_file: Path,
    *,
    config: ScoringConfig,
    topics: TopicsConfig,
    data_dir: Path = DATA_DIR,
    exclude: AbstractSet[str] = frozenset(),
    now: dt.datetime | None = None,
) -> PicksFile:
    candidates = CandidatesFile.model_validate_json(candidates_file.read_bytes())
    today = candidates.date
    history, broken = load_history(data_dir, today)
    scored = score_all(candidates.papers, config, today)
    picks, notes, light_day = choose(
        scored, config=config, topics=topics, today=today, history=history, exclude=exclude
    )
    notes = [f"Could not read old picks file {name}" for name in broken] + notes
    try:
        relative = str(candidates_file.resolve().relative_to(data_dir.resolve()))
    except ValueError:
        relative = str(candidates_file)
    return PicksFile(
        date=today,
        generated_at=now or dt.datetime.now(dt.UTC),
        candidates_file=relative,
        light_day=light_day,
        notes=notes,
        picks=picks,
    )


def write(picks: PicksFile, data_dir: Path = DATA_DIR) -> Path:
    path = data_dir / PICKS_SUBDIR / f"{picks.date.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(picks.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
