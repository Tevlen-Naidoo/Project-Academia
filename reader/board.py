"""The reading board: the papers Tevlen chose to read, in three columns.

Stored in Firestore (boards/main, see store/data.py), or in data/board.json when
Firestore isn't set up, and exported to schema/ for the web app. Cards are only
ever added by hand (the `board` commands or the web app); recommendations never
land here on their own. Scoring and the prerequisite finder call
`Board.paper_ids()` to skip papers already on the board, Done included.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fetcher.models import PaperIds
from reader.models import PaperId, PaperRef

SCHEMA_VERSION: Literal[1] = 1
BOARD_PATH = Path("data/board.json")
TO_READ_LIMIT = 10
"""The most papers To read may hold, so it stays a short list and not a backlog."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class BoardError(ValueError):
    """A board change that is not allowed, with a message fit to show the user."""


class Column(StrEnum):
    TO_READ = "to_read"
    READING = "reading"
    DONE = "done"


class SourceKind(StrEnum):
    PICK = "pick"
    """One of the daily picks."""
    PREREQUISITE = "prerequisite"
    """A Read first, Foundation or Background paper shown under a pick."""
    MANUAL = "manual"
    """Any other paper, added by id or link."""


class CardSource(_Model):
    """Where the paper was found when it was added."""

    kind: SourceKind = SourceKind.MANUAL
    date: dt.date | None = None
    """The picks or prerequisites file it came from."""
    pick_id: PaperId | None = None
    """For a prerequisite: the pick it was shown under."""

    @model_validator(mode="after")
    def _pick_only_on_prerequisite(self) -> Self:
        if self.pick_id is not None and self.kind is not SourceKind.PREREQUISITE:
            raise ValueError("pick_id is only set for a prerequisite")
        return self


class Card(_Model):
    paper: PaperRef
    column: Column = Column.TO_READ
    source: CardSource = Field(default_factory=CardSource)
    added_on: dt.date
    started_on: dt.date | None = None
    """When it moved to Reading."""
    finished_on: dt.date | None = None
    """When it moved to Done."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        links = self.paper.links
        if not (links.abstract_page or links.pdf or links.doi):
            raise ValueError("a card needs a link to the paper so it can be revisited")
        if (self.column is Column.TO_READ) and (self.started_on or self.finished_on):
            raise ValueError("a To read card has no start or finish date")
        if self.column is Column.READING and (not self.started_on or self.finished_on):
            raise ValueError("a Reading card has a start date and no finish date")
        if self.column is Column.DONE and not self.finished_on:
            raise ValueError("a Done card has a finish date")
        return self

    def matches(self, ids: PaperIds) -> bool:
        """True when any id in `ids` names this card's paper."""
        own = self.paper.ids
        return any(
            mine is not None and mine.lower() == theirs.lower()
            for mine, theirs in (
                (own.arxiv, ids.arxiv),
                (own.doi, ids.doi),
                (own.s2, ids.s2),
                (own.openalex, ids.openalex),
            )
            if theirs is not None
        )


class Board(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    updated_at: AwareDatetime | None = None
    cards: list[Card] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        seen: set[str] = set()
        for card in self.cards:
            if card.paper.id in seen:
                raise ValueError(f"duplicate paper id {card.paper.id}")
            seen.add(card.paper.id)
        if len(self.column(Column.TO_READ)) > TO_READ_LIMIT:
            raise ValueError(f"To read holds at most {TO_READ_LIMIT} papers")
        return self

    def paper_ids(self) -> set[str]:
        """Every paper on the board, in any column. Future picks skip these."""
        out: set[str] = set()
        for card in self.cards:
            out.add(card.paper.id)
            ids = card.paper.ids
            out.update(
                f"{prefix}:{value}"
                for prefix, value in (
                    ("arxiv", ids.arxiv),
                    ("doi", ids.doi.lower() if ids.doi else None),
                    ("openalex", ids.openalex),
                    ("s2", ids.s2),
                )
                if value
            )
        return out

    def column(self, column: Column) -> list[Card]:
        return [c for c in self.cards if c.column is column]

    def find(self, ids: PaperIds) -> Card | None:
        return next((c for c in self.cards if c.matches(ids)), None)

    def add(self, paper: PaperRef, *, today: dt.date, source: CardSource | None = None) -> Card:
        self.check_can_add(paper.ids)
        card = Card(paper=paper, source=source or CardSource(), added_on=today)
        self.cards = [*self.cards, card]
        return card

    def check_can_add(self, ids: PaperIds) -> None:
        """Raise `BoardError` if the paper is already on the board or To read is full."""
        existing = self.find(ids)
        if existing is not None:
            where = _COLUMN_NAMES[existing.column]
            raise BoardError(f"{existing.paper.title!r} is already on the board, in {where}")
        if len(self.column(Column.TO_READ)) >= TO_READ_LIMIT:
            raise BoardError(
                f"To read already holds {TO_READ_LIMIT} papers. "
                "Start, finish or remove one before adding another."
            )

    def move(self, ids: PaperIds, column: Column, *, today: dt.date) -> Card:
        card = self._get(ids)
        if card.column is column:
            return card
        if column is Column.TO_READ and len(self.column(Column.TO_READ)) >= TO_READ_LIMIT:
            raise BoardError(f"To read already holds {TO_READ_LIMIT} papers.")
        data = card.model_dump()
        data["column"] = column
        if column is Column.TO_READ:
            data["started_on"] = data["finished_on"] = None
        elif column is Column.READING:
            data["started_on"] = card.started_on or today
            data["finished_on"] = None
        else:
            data["started_on"] = card.started_on or today
            data["finished_on"] = today
        moved = Card.model_validate(data)
        # Moving to a column puts the card at the end of it.
        self.cards = [*(c for c in self.cards if c is not card), moved]
        return moved

    def remove(self, ids: PaperIds) -> Card:
        card = self._get(ids)
        self.cards = [c for c in self.cards if c is not card]
        return card

    def _get(self, ids: PaperIds) -> Card:
        card = self.find(ids)
        if card is None:
            raise BoardError(f"{ids.canonical()} is not on the board")
        return card


_COLUMN_NAMES = {Column.TO_READ: "To read", Column.READING: "Reading", Column.DONE: "Done"}


def column_name(column: Column) -> str:
    return _COLUMN_NAMES[column]


def load(path: Path = BOARD_PATH) -> Board:
    """The board at `path`, or an empty board when the file does not exist yet."""
    if not path.exists():
        return Board()
    return Board.model_validate_json(path.read_bytes())


def save(board: Board, path: Path = BOARD_PATH, *, now: dt.datetime | None = None) -> Path:
    board.updated_at = now or dt.datetime.now(dt.UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(board.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    return path
