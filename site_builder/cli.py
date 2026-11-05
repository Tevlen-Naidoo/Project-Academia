"""Command line entry point: `python -m site_builder build`."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import functools
import http.server
import os
from collections.abc import Sequence
from pathlib import Path

from fetcher.pipeline import DATA_DIR
from fetcher.topics import DEFAULT_TOPICS_PATH, load_topics
from notifier import vapid
from site_builder import load, render

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "site"


def _push_key() -> str:
    """The VAPID public key, worked out from the private key secret when it is set."""
    secret = os.environ.get(vapid.ENV_VAR, "")
    if not secret.strip():
        return ""
    try:
        return vapid.public_key(vapid.load_private_key(secret))
    except ValueError as exc:
        # A bad key shouldn't take the site down; it only leaves notifications off.
        print(f"Notifications stay off: {vapid.ENV_VAR} isn't a usable key ({exc}).")
        return ""


def _cmd_build(args: argparse.Namespace) -> int:
    site = render.Site(
        repo=args.repo,
        topics=load_topics(args.topics),
        data=load.load(args.data_dir),
        built_at=dt.datetime.now(dt.UTC),
        push_key=_push_key(),
    )
    pages = render.build(site, args.out)
    print(f"{len(pages)} pages, {len(site.data.days)} days -> {args.out}")
    if args.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(args.out))
        with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"Serving on http://127.0.0.1:{args.port}/ (Ctrl+C to stop)")
            with contextlib.suppress(KeyboardInterrupt):
                server.serve_forever()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="site_builder", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the static site into site/")
    build.add_argument("--data-dir", type=Path, default=DATA_DIR)
    build.add_argument("--out", type=Path, default=DEFAULT_OUT)
    build.add_argument("--topics", type=Path, default=DEFAULT_TOPICS_PATH)
    build.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", render.DEFAULT_REPO),
        help="owner/name of the GitHub repository that holds the board",
    )
    build.add_argument("--serve", action="store_true", help="serve the site after building")
    build.add_argument("--port", type=int, default=8000)
    build.set_defaults(func=_cmd_build)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
