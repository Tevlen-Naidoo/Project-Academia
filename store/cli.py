"""Command line entry point.

python -m store setup --owner UID   # allow your account and copy data/ into Firestore (once)
python -m store check               # show what Firestore holds
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from fetcher.pipeline import DATA_DIR
from reader import board
from store import data
from store.firestore import ENV_VAR, Firestore, StoreError

_UID = re.compile(r"^[A-Za-z0-9]{10,128}$")


def _connect() -> Firestore:
    db = data.connect()
    if db is None:
        raise StoreError(f"{ENV_VAR} is not set: add the service account key first")
    return db


def _cmd_setup(args: argparse.Namespace) -> int:
    owner = (args.owner or "").strip() or None
    if owner and not _UID.match(owner):
        print(f"That doesn't look like a Firebase account id: {owner!r}", file=sys.stderr)
        return 1
    with _connect() as db:
        for line in data.import_from_files(db, args.data_dir, owner=owner):
            print(line)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    with _connect() as db:
        b = data.load_board(args.data_dir, db)
        counts = ", ".join(f"{board.column_name(c)} {len(b.column(c))}" for c in board.Column)
        print(f"Board: {counts}")
        print(f"Notification devices: {len(data.load_devices(args.data_dir, db))}")
        owners = [doc.id for doc in db.list(data.OWNERS)]
        print(f"Allowed accounts: {len(owners)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="store", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="allow an account and copy data/ into Firestore")
    setup.add_argument("--owner", help="your Firebase account id, from the site's Settings page")
    setup.add_argument("--data-dir", type=Path, default=DATA_DIR)
    setup.set_defaults(func=_cmd_setup)
    check = sub.add_parser("check", help="show what Firestore holds")
    check.add_argument("--data-dir", type=Path, default=DATA_DIR)
    check.set_defaults(func=_cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return result
