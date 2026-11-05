"""The devices that turned on notifications.

The site's Settings page saves each device to Firestore (see store/data.py and
site_builder/static/js/push.js); data/push/subscriptions.json is only read when
Firestore isn't set up. An endpoint is only useful together with this project's
VAPID private key, which stays in the Actions secrets.
"""

from __future__ import annotations

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


def load(path: Path) -> SubscriptionsFile:
    if not path.exists():
        return SubscriptionsFile()
    return SubscriptionsFile.model_validate_json(path.read_bytes())


def save(file: SubscriptionsFile, path: Path, now: dt.datetime) -> None:
    file.updated_at = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(file.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
