"""Command line entry point: `python -m reader <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path

from fetcher.enrich import presort_key
from fetcher.http import HttpClient
from fetcher.models import CandidatesFile, Paper
from reader import finder, schema

DEFAULT_PICKS = 3


def choose_picks(papers: Sequence[Paper], ids: Sequence[str], limit: int) -> list[Paper]:
    """The papers named by `ids`, or the top `limit` by the fetcher's rough order.

    The fallback stands in for step 2 (scoring) until it writes a picks file.
    """
    if ids:
        by_id = {p.id: p for p in papers}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise SystemExit(f"not in the file: {', '.join(missing)}")
        return [by_id[i] for i in ids]
    return sorted(papers, key=presort_key, reverse=True)[:limit]


def _cmd_prereqs(args: argparse.Namespace) -> int:
    papers = CandidatesFile.model_validate_json(args.file.read_bytes()).papers
    picks = choose_picks(papers, args.ids, args.limit)
    options = finder.FinderOptions(
        today=args.date,
        foundation_hops=args.hops,
        exclude=finder.history(args.data_dir, args.date),
    )
    with HttpClient() as http:
        result = finder.run(http, picks, options)
    path = finder.write(result, args.data_dir)
    for pick in result.picks:
        print(f"{pick.pick_title} ({pick.source}, {pick.references_seen} references)")
        for pre in pick.prerequisites:
            print(f"  {pre.label:<11} {pre.paper.title}")
        for error in pick.errors[:5]:
            print(f"  ! {error}", file=sys.stderr)
    print(f"-> {path}")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.check:
        out_of_date = schema.stale()
        if out_of_date:
            names = ", ".join(out_of_date)
            print(f"Out of date: {names}. Run `python -m reader schema`.", file=sys.stderr)
            return 1
        print("Schemas are up to date.")
        return 0
    for path in schema.write_all():
        print(f"wrote {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reader", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prereqs = sub.add_parser("prereqs", help="find prerequisite reading for the day's picks")
    prereqs.add_argument("file", type=Path, help="a candidates (or picks) file")
    prereqs.add_argument("--ids", nargs="*", default=[], help="paper ids of the picks")
    prereqs.add_argument(
        "--limit", type=int, default=DEFAULT_PICKS, help="picks to take when --ids is not given"
    )
    prereqs.add_argument(
        "--date", type=dt.date.fromisoformat, default=dt.datetime.now(dt.UTC).date()
    )
    prereqs.add_argument("--hops", type=int, default=finder.FOUNDATION_MAX_HOPS)
    prereqs.add_argument("--data-dir", type=Path, default=finder.DATA_DIR)
    prereqs.set_defaults(func=_cmd_prereqs)

    schema_cmd = sub.add_parser("schema", help="write the prerequisites JSON Schema")
    schema_cmd.add_argument("--check", action="store_true", help="fail if it is out of date")
    schema_cmd.set_defaults(func=_cmd_schema)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
