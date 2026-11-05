"""Command line entry point: `python -m site_builder build`."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import functools
import http.server
import json
import os
import re
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


FIREBASE_ENV = "FIREBASE_CONFIG"
_FIREBASE_KEYS = {"apiKey", "authDomain", "projectId", "appId"}


def firebase_config() -> str:
    """The Firebase web config from FIREBASE_CONFIG, checked and made compact.

    Accepts the JSON object, or the `const firebaseConfig = {...};` snippet the
    Firebase console shows, as long as its keys are quoted or plain names."""
    raw = os.environ.get(FIREBASE_ENV, "").strip()
    if not raw:
        return ""
    # The console's whole <script> snippet has other braces (imports), so start
    # from `firebaseConfig = {` when it is there.
    start = raw.find("{", max(raw.find("firebaseConfig"), 0))
    end = raw.rfind("}")
    text = raw[start : end + 1] if start >= 0 else raw
    try:
        data = json.loads(text)
    except ValueError:
        # The console snippet uses bare keys: apiKey: "..." -> "apiKey": "..."
        try:
            quoted = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', text)
            data = json.loads(re.sub(r",\s*}", "}", quoted))
        except ValueError:
            data = None
    if not isinstance(data, dict) or not data.keys() >= _FIREBASE_KEYS:
        print(f"Sign-in stays off: {FIREBASE_ENV} isn't a Firebase web app config.")
        return ""
    return json.dumps({k: str(v) for k, v in data.items()}, separators=(",", ":"))


def _cmd_build(args: argparse.Namespace) -> int:
    site = render.Site(
        repo=args.repo,
        topics=load_topics(args.topics),
        data=load.load(args.data_dir),
        built_at=dt.datetime.now(dt.UTC),
        push_key=_push_key(),
        firebase_config=firebase_config(),
    )
    try:
        pages = render.build(site, args.out, with_assets=not args.no_assets)
    except render.MissingAssetsError as exc:
        print(exc)
        return 1
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
    build.add_argument(
        "--no-assets",
        action="store_true",
        help="leave out the built web components (used by `npm run dev`, which serves them)",
    )
    build.set_defaults(func=_cmd_build)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
