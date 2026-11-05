"""Where the board and the notification devices live.

With FIREBASE_SERVICE_ACCOUNT set, both are in Firestore:

- `boards/main`: the reading board, in the same shape as `reader.board.Board`.
- `devices/<sha256 of the endpoint>`: one push subscription per device.
- `owners/<uid>`: the Firebase accounts the security rules let read and write.

Without it (local development, tests), the board is read from and written to
`data/board.json` and the devices from `data/push/subscriptions.json`, as before.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from notifier.subscriptions import Subscription, SubscriptionsFile
from notifier.subscriptions import load as load_subscriptions_file
from notifier.subscriptions import save as save_subscriptions_file
from reader import board
from store.firestore import ENV_VAR, Conflict, Firestore, ServiceAccount

BOARD_DOC = "boards/main"
DEVICES = "devices"
OWNERS = "owners"


def connect() -> Firestore | None:
    """A Firestore client when the service account key is set, else None."""
    account = ServiceAccount.from_env()
    return Firestore(account) if account else None


def device_id(endpoint: str) -> str:
    """The device document's id; the site works it out the same way."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _board_file(data_dir: Path) -> Path:
    return data_dir / board.BOARD_PATH.name


def _warn_local(what: str) -> None:
    print(f"note: {ENV_VAR} is not set, so the {what} comes from data/.", file=sys.stderr)


# --- board ------------------------------------------------------------------------


def load_board(data_dir: Path, db: Firestore | None = None) -> board.Board:
    """The reading board. Raises `StoreError` if Firestore is set up but can't be read,
    so the daily run stops rather than re-picking papers you've saved."""
    db = db or connect()
    if db is None:
        _warn_local("reading board")
        return board.load(_board_file(data_dir))
    doc = db.get(BOARD_DOC)
    return board.Board.model_validate(doc.data) if doc else board.Board()


def change_board[T](
    data_dir: Path,
    apply: Callable[[board.Board], T],
    *,
    db: Firestore | None = None,
    now: dt.datetime | None = None,
) -> T:
    """Load the board, run `apply` on it, and save it. Retries once if the site
    saved in between. `apply` may raise `BoardError` to stop without saving."""
    db = db or connect()
    if db is None:
        path = _board_file(data_dir)
        current = board.load(path)
        result = apply(current)
        board.save(current, path, now=now)
        return result
    for attempt in (1, 2):
        doc = db.get(BOARD_DOC)
        current = board.Board.model_validate(doc.data) if doc else board.Board()
        result = apply(current)
        current.updated_at = now or dt.datetime.now(dt.UTC)
        try:
            db.set(
                BOARD_DOC,
                current.model_dump(mode="json", exclude_none=True),
                if_update_time=doc.update_time if doc else "",
            )
        except Conflict:
            if attempt == 2:
                raise
            continue
        return result
    raise AssertionError("unreachable")  # pragma: no cover


# --- devices ----------------------------------------------------------------------


def _devices_file(data_dir: Path) -> Path:
    return data_dir / "push" / "subscriptions.json"


def load_devices(data_dir: Path, db: Firestore | None = None) -> list[Subscription]:
    db = db or connect()
    if db is None:
        _warn_local("device list")
        return load_subscriptions_file(_devices_file(data_dir)).subscriptions
    return [Subscription.model_validate(doc.data) for doc in db.list(DEVICES)]


def save_device(data_dir: Path, sub: Subscription, db: Firestore | None = None) -> None:
    db = db or connect()
    if db is None:
        path = _devices_file(data_dir)
        file = load_subscriptions_file(path)
        file.subscriptions = [s for s in file.subscriptions if s.endpoint != sub.endpoint]
        file.subscriptions.append(sub)
        save_subscriptions_file(file, path, dt.datetime.now(dt.UTC))
        return
    db.set(f"{DEVICES}/{device_id(sub.endpoint)}", sub.model_dump(mode="json", exclude_none=True))


def forget_devices(data_dir: Path, endpoints: Iterable[str], db: Firestore | None = None) -> None:
    gone = set(endpoints)
    if not gone:
        return
    db = db or connect()
    if db is None:
        path = _devices_file(data_dir)
        file = load_subscriptions_file(path)
        file.subscriptions = [s for s in file.subscriptions if s.endpoint not in gone]
        save_subscriptions_file(file, path, dt.datetime.now(dt.UTC))
        return
    for endpoint in gone:
        db.delete(f"{DEVICES}/{device_id(endpoint)}")


# --- one-time move from data/ -------------------------------------------------------


def import_from_files(db: Firestore, data_dir: Path, *, owner: str | None) -> list[str]:
    """Copy data/board.json and data/push/subscriptions.json into Firestore and allow
    `owner` (a Firebase user id). Never overwrites a board that is already there."""
    done: list[str] = []
    if owner:
        db.set(f"{OWNERS}/{owner}", {"added_on": dt.datetime.now(dt.UTC).date().isoformat()})
        done.append(f"allowed account {owner}")
    path = _board_file(data_dir)
    if path.exists():
        local = board.load(path)
        if db.get(BOARD_DOC) is None:
            db.set(BOARD_DOC, local.model_dump(mode="json", exclude_none=True), if_update_time="")
            done.append(f"copied the board ({len(local.cards)} cards)")
        else:
            done.append("kept the board already in Firestore")
    devices: SubscriptionsFile = load_subscriptions_file(_devices_file(data_dir))
    for sub in devices.subscriptions:
        save_device(data_dir, sub, db)
    if devices.subscriptions:
        done.append(f"copied {len(devices.subscriptions)} notification device(s)")
    return done
