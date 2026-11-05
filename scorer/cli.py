"""Command line entry point: `python -m scorer <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from fetcher.pipeline import DATA_DIR
from fetcher.topics import DEFAULT_TOPICS_PATH, load_topics
from reader import board
from scorer import pipeline, schema
from scorer.config import DEFAULT_SCORING_PATH, load_scoring
from scorer.models import PicksFile
from store.data import load_board


def _cmd_run(args: argparse.Namespace) -> int:
    source: Path = args.candidates or pipeline.candidates_path(args.date, args.data_dir)
    if not source.exists():
        print(f"No candidates file at {source}", file=sys.stderr)
        return 1
    picks = pipeline.run(
        source,
        config=load_scoring(args.config),
        topics=load_topics(args.topics),
        data_dir=args.data_dir,
        # Firestore when FIREBASE_SERVICE_ACCOUNT is set, else --board or data/board.json.
        exclude=(board.load(args.board) if args.board else load_board(args.data_dir)).paper_ids(),
    )
    path = pipeline.write(picks, args.data_dir)
    light = " (light day)" if picks.light_day else ""
    print(f"{len(picks.picks)} picks for {picks.date}{light} -> {path}")
    for pick in picks.picks:
        tag = f"[{pick.topic}, {pick.funnel}, {pick.adjusted:.3f}]"
        print(f"  {pick.rank}. {tag} {pick.paper.title}")
        print(f"     {pick.reason}")
    for note in picks.notes:
        print(f"  note: {note}")
    return 0 if picks.picks else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    failed = 0
    for path in args.files:
        try:
            PicksFile.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as exc:
            failed += 1
            print(f"FAIL {path}\n{exc}", file=sys.stderr)
        else:
            print(f"ok   {path}")
    return 1 if failed else 0


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.check:
        out_of_date = schema.stale()
        if out_of_date:
            names = ", ".join(out_of_date)
            print(f"Out of date: {names}. Run `python -m scorer schema`.", file=sys.stderr)
            return 1
        print("Schemas are up to date.")
        return 0
    for path in schema.write_all():
        print(f"wrote {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scorer", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="pick the day's papers from its candidates file")
    run.add_argument("--date", type=dt.date.fromisoformat, default=dt.datetime.now(dt.UTC).date())
    run.add_argument(
        "--candidates", type=Path, help="candidates file (default: the one for --date)"
    )
    run.add_argument("--config", type=Path, default=DEFAULT_SCORING_PATH)
    run.add_argument("--topics", type=Path, default=DEFAULT_TOPICS_PATH)
    run.add_argument("--data-dir", type=Path, default=DATA_DIR)
    run.add_argument(
        "--board", type=Path, help="reading board to skip (default: board.json in --data-dir)"
    )
    run.set_defaults(func=_cmd_run)

    validate = sub.add_parser("validate", help="validate picks files against the model")
    validate.add_argument("files", type=Path, nargs="+")
    validate.set_defaults(func=_cmd_validate)

    schema_cmd = sub.add_parser("schema", help="write the picks JSON Schema to schema/")
    schema_cmd.add_argument("--check", action="store_true", help="fail if it is out of date")
    schema_cmd.set_defaults(func=_cmd_schema)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
