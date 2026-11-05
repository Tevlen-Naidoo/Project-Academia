"""Command line entry point.

python -m notifier keys                 # make a VAPID key pair (once)
python -m notifier send --date DAY      # tell every device the day's picks are up
python -m notifier send --test          # a test notification
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric import ec

from fetcher.pipeline import DATA_DIR
from notifier import subscriptions, vapid, webpush
from scorer.models import PicksFile

DEFAULT_REPO = "Tevlen-Naidoo/Project-Academia"
TITLE_LIMIT = 90


def site_url(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def _short(title: str) -> str:
    title = " ".join(title.split())
    return title if len(title) <= TITLE_LIMIT else title[: TITLE_LIMIT - 1].rstrip() + "…"


def daily_message(picks: PicksFile, today: dt.date) -> dict[str, str]:
    n = len(picks.picks)
    when = "today" if picks.date == today else f"{picks.date:%A} {picks.date.day} {picks.date:%B}"
    return {
        "title": f"{n} new paper{'s' if n != 1 else ''} for {when}",
        "body": "\n".join("• " + _short(p.paper.title) for p in picks.picks),
        "date": picks.date.isoformat(),
        "tag": "daily",
    }


def test_message(today: dt.date) -> dict[str, str]:
    return {
        "title": "Notifications are on",
        "body": "This is a test from Project Academia. Tap to open today's papers.",
        "date": today.isoformat(),
        "tag": "test",
    }


def _cmd_keys(args: argparse.Namespace) -> int:
    private = vapid.generate()
    print("Private key (save it as the VAPID_PRIVATE_KEY Actions secret, and nowhere else):")
    print(private)
    print()
    print("Public key (the site works this out by itself; shown for reference):")
    print(vapid.public_key(vapid.load_private_key(private)))
    return 0


def _key() -> ec.EllipticCurvePrivateKey | None:
    secret = os.environ.get(vapid.ENV_VAR, "")
    return vapid.load_private_key(secret) if secret.strip() else None


def _cmd_add_device(args: argparse.Namespace) -> int:
    try:
        sub = subscriptions.decode_code(args.code)
    except ValueError as exc:
        print(f"Couldn't read the code: {exc}", file=sys.stderr)
        return 1
    today = dt.datetime.now(dt.UTC).date()
    sub = sub.model_copy(update={"added_on": today})
    path: Path = args.data_dir / "push" / "subscriptions.json"
    file = subscriptions.load(path)
    subscriptions.upsert(file, sub)
    subscriptions.save(file, path, dt.datetime.now(dt.UTC))
    print(f"Saved {sub.device or 'the device'}. {len(file.subscriptions)} device(s) signed up.")

    key = _key()
    if key is None:
        print(f"{vapid.ENV_VAR} is not set, so no test notification was sent.")
        return 0
    payload = json.dumps(test_message(today), ensure_ascii=False).encode("utf-8")
    with httpx.Client(timeout=30) as client:
        result = webpush.send(client, sub, payload, key, site_url(args.repo))
    if result.ok:
        print("Sent a test notification to it.")
        return 0
    print(f"The test notification failed: {result.status} {result.detail}", file=sys.stderr)
    return 1


def _cmd_send(args: argparse.Namespace) -> int:
    key = _key()
    if key is None:
        print(f"{vapid.ENV_VAR} is not set, so no notifications were sent.")
        return 0

    path: Path = args.data_dir / "push" / "subscriptions.json"
    file = subscriptions.load(path)
    if not file.subscriptions:
        print("No devices have turned on notifications yet.")
        return 0

    today = dt.datetime.now(dt.UTC).date()
    if args.test:
        message = test_message(today)
    else:
        day = args.date or today
        picks_path = args.data_dir / "picks" / f"{day.isoformat()}.json"
        if not picks_path.exists():
            print(f"No picks for {day}, so no notification.")
            return 0
        picks = PicksFile.model_validate_json(picks_path.read_bytes())
        if not picks.picks:
            print(f"No picks for {day}, so no notification.")
            return 0
        message = daily_message(picks, today)

    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    subject = site_url(args.repo)
    with httpx.Client(timeout=30) as client:
        results = [webpush.send(client, sub, payload, key, subject) for sub in file.subscriptions]

    for r in results:
        label = r.subscription.device or "device"
        state = "sent" if r.ok else ("gone, removed" if r.gone else f"failed: {r.detail}")
        print(f"{label}: {r.status} {state}")

    gone = [r for r in results if r.gone]
    if gone:
        file.subscriptions = [r.subscription for r in results if not r.gone]
        subscriptions.save(file, path, dt.datetime.now(dt.UTC))
    sent = sum(r.ok for r in results)
    print(f"Sent to {sent} of {len(results)} devices.")
    failed = [r for r in results if not r.ok and not r.gone]
    return 1 if failed and not sent else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notifier", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    keys = sub.add_parser("keys", help="print a new VAPID key pair")
    keys.set_defaults(func=_cmd_keys)
    send = sub.add_parser("send", help="send a notification to every subscribed device")
    send.add_argument("--date", type=dt.date.fromisoformat, default=None)
    send.add_argument("--test", action="store_true", help="send a test message instead")
    send.add_argument("--data-dir", type=Path, default=DATA_DIR)
    send.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    send.set_defaults(func=_cmd_send)
    add = sub.add_parser("add-device", help="save a device from its sign-up code")
    add.add_argument("code")
    add.add_argument("--data-dir", type=Path, default=DATA_DIR)
    add.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    add.set_defaults(func=_cmd_add_device)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    sys.stdout.flush()
    return result
