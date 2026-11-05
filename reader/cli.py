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
from reader import board, finder, schema
from reader.board import BoardError, CardSource, Column, SourceKind
from reader.lookup import PaperLookup, parse_ids

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


def _board_paths(args: argparse.Namespace) -> tuple[Path, board.Board]:
    path: Path = args.board
    return path, board.load(path)


def _cmd_board_add(args: argparse.Namespace) -> int:
    path, current = _board_paths(args)
    ids = parse_ids(args.paper)
    if args.prereq_of:
        source = CardSource(
            kind=SourceKind.PREREQUISITE, date=args.date_found, pick_id=args.prereq_of
        )
    elif args.pick:
        source = CardSource(kind=SourceKind.PICK, date=args.date_found)
    else:
        source = CardSource(kind=SourceKind.MANUAL, date=args.date_found)
    # Check before the network lookup, so a duplicate or a full column fails fast.
    current.check_can_add(ids)
    with HttpClient() as http:
        paper = PaperLookup(http, data_dir=args.data_dir).resolve(ids)
    card = current.add(paper, today=args.today, source=source)
    board.save(current, path)
    print(f"Added to To read: {card.paper.title}")
    _print_links(card)
    return 0


def _cmd_board_move(args: argparse.Namespace) -> int:
    path, current = _board_paths(args)
    card = current.move(parse_ids(args.paper), Column(args.column), today=args.today)
    board.save(current, path)
    print(f"Moved to {board.column_name(card.column)}: {card.paper.title}")
    return 0


def _cmd_board_remove(args: argparse.Namespace) -> int:
    path, current = _board_paths(args)
    card = current.remove(parse_ids(args.paper))
    board.save(current, path)
    print(f"Removed: {card.paper.title}")
    return 0


def _print_links(card: board.Card) -> None:
    links = card.paper.links
    for label, url in (("page", links.abstract_page), ("pdf", links.pdf), ("doi", links.doi)):
        if url is not None:
            print(f"    {label}: {url}")


def _cmd_board_list(args: argparse.Namespace) -> int:
    _, current = _board_paths(args)
    for column in Column:
        cards = current.column(column)
        limit = f"/{board.TO_READ_LIMIT}" if column is Column.TO_READ else ""
        print(f"{board.column_name(column)} ({len(cards)}{limit})")
        for card in cards:
            when = card.finished_on or card.started_on or card.added_on
            print(f"  {card.paper.id:<32} {when}  {card.paper.title}")
            _print_links(card)
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

    _add_board_parser(sub)

    schema_cmd = sub.add_parser("schema", help="write the prerequisites and board JSON Schemas")
    schema_cmd.add_argument("--check", action="store_true", help="fail if it is out of date")
    schema_cmd.set_defaults(func=_cmd_schema)
    return parser


def _add_board_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    today = dt.datetime.now(dt.UTC).date()
    board_cmd = sub.add_parser("board", help="your reading board: To read, Reading, Done")
    board_cmd.add_argument("--board", type=Path, default=board.BOARD_PATH)
    board_cmd.add_argument("--today", type=dt.date.fromisoformat, default=today)
    actions = board_cmd.add_subparsers(dest="action", required=True)

    add = actions.add_parser("add", help="add a paper to To read")
    add.add_argument("paper", help="an arXiv id, a DOI, or an arXiv, DOI or Semantic Scholar link")
    found = add.add_mutually_exclusive_group()
    found.add_argument("--pick", action="store_true", help="it was one of the daily picks")
    found.add_argument("--prereq-of", metavar="PICK_ID", help="it was shown under this pick")
    add.add_argument("--date-found", type=dt.date.fromisoformat, help="the day it was recommended")
    add.add_argument("--data-dir", type=Path, default=finder.DATA_DIR)
    add.set_defaults(func=_cmd_board_add)

    for name, column, help_text in (
        ("start", Column.READING, "move a paper to Reading"),
        ("done", Column.DONE, "move a paper to Done"),
        ("later", Column.TO_READ, "move a paper back to To read"),
    ):
        move = actions.add_parser(name, help=help_text)
        move.add_argument("paper", help="the paper's id or link, as when it was added")
        move.set_defaults(func=_cmd_board_move, column=column.value)

    remove = actions.add_parser("remove", help="take a paper off the board")
    remove.add_argument("paper")
    remove.set_defaults(func=_cmd_board_remove)

    show = actions.add_parser("list", help="show the board")
    show.set_defaults(func=_cmd_board_list)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return result
