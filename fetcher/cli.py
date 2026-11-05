"""Command line entry point: `python -m fetcher <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from fetcher import pipeline, schema
from fetcher.http import HttpClient
from fetcher.models import CandidatesFile
from fetcher.topics import DEFAULT_TOPICS_PATH, load_topics


def _cmd_topics(args: argparse.Namespace) -> int:
    config = load_topics(args.config)
    for tag, topic in config.topics.items():
        arxiv = ", ".join(r.category for r in topic.arxiv) or "-"
        openalex = ", ".join(str(r.subfield) for r in topic.openalex) or "-"
        print(f"{tag:<18}{topic.weight:<6}arXiv: {arxiv}  |  OpenAlex: {openalex}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    failed = 0
    for path in args.files:
        try:
            CandidatesFile.model_validate_json(path.read_bytes())
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
            print(f"Out of date: {names}. Run `python -m fetcher schema`.", file=sys.stderr)
            return 1
        print("Schemas are up to date.")
        return 0
    for path in schema.write_all():
        print(f"wrote {path}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    topics = load_topics(args.config)
    options = pipeline.RunOptions(today=args.date, fresh_days=args.fresh_days)
    with HttpClient() as http:
        candidates = pipeline.run(
            http, topics, options, log=lambda line: print(f"[done] {line}", flush=True)
        )
    path = pipeline.write(candidates, args.data_dir)
    for name, report in candidates.sources.items():
        print(
            f"{name}: {report.fetched} fetched, {len(report.errors)} errors, {report.duration_s}s"
        )
        for error in report.errors[:10]:
            print(f"  - {error}", file=sys.stderr)
    print(f"{len(candidates.papers)} candidates -> {path}")
    per_topic = Counter(t for p in candidates.papers for t in p.topics)
    for tag in topics.topics:
        print(f"  {tag:<18}{per_topic[tag]}")
    return 0 if candidates.papers else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fetcher", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch today's candidates and write them to data/")
    run.add_argument("--date", type=dt.date.fromisoformat, default=dt.datetime.now(dt.UTC).date())
    run.add_argument("--fresh-days", type=int, default=pipeline.FRESH_WINDOW_DAYS)
    run.add_argument("--config", type=Path, default=DEFAULT_TOPICS_PATH)
    run.add_argument("--data-dir", type=Path, default=pipeline.DATA_DIR)
    run.set_defaults(func=_cmd_run)

    topics = sub.add_parser("topics", help="show the topic mapping")
    topics.add_argument("--config", type=Path, default=DEFAULT_TOPICS_PATH)
    topics.set_defaults(func=_cmd_topics)

    validate = sub.add_parser("validate", help="validate candidates files against the model")
    validate.add_argument("files", type=Path, nargs="+")
    validate.set_defaults(func=_cmd_validate)

    schema_cmd = sub.add_parser("schema", help="write the JSON Schemas to schema/")
    schema_cmd.add_argument("--check", action="store_true", help="fail if they are out of date")
    schema_cmd.set_defaults(func=_cmd_schema)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
