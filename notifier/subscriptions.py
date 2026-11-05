"""data/push/subscriptions.json: the devices that turned on notifications.

The site's Settings page writes this file through the GitHub API (see
site_builder/static/push.js). An endpoint is only useful together with this
project's VAPID private key, which stays in the Actions secrets.
"""

from __future__ import annotations

import base64
import datetime as dt
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Keys(BaseModel):
    model_config = ConfigDict(extra="ignore")
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class Subscription(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(pattern=r"^https://")
    keys: Keys
    device: str | None = None
    added_on: dt.date | None = None


class SubscriptionsFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = 1
    updated_at: dt.datetime | None = None
    subscriptions: list[Subscription] = Field(default_factory=list)


CODE_PREFIX = "pa1."


def decode_code(code: str) -> Subscription:
    """A device's sign-up code from the Settings page: `pa1.` + base64url JSON."""
    text = "".join(code.split())
    if not text.startswith(CODE_PREFIX):
        raise ValueError("That isn't a sign-up code from the Settings page (it starts with pa1.).")
    body = text[len(CODE_PREFIX) :]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError as exc:
        raise ValueError("The sign-up code is cut short or changed. Copy it again.") from exc
    return Subscription.model_validate_json(raw)


def upsert(file: SubscriptionsFile, sub: Subscription) -> None:
    file.subscriptions = [s for s in file.subscriptions if s.endpoint != sub.endpoint]
    file.subscriptions.append(sub)


def load(path: Path) -> SubscriptionsFile:
    if not path.exists():
        return SubscriptionsFile()
    return SubscriptionsFile.model_validate_json(path.read_bytes())


def save(file: SubscriptionsFile, path: Path, now: dt.datetime) -> None:
    file.updated_at = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(file.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
