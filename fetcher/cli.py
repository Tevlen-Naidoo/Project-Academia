"""Command line entry point: `python -m fetcher <command>`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from fetcher import schema
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fetcher", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

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
