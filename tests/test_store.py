from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from fetcher.models import Links, PaperIds
from notifier.subscriptions import Keys, Subscription, SubscriptionsFile
from notifier.subscriptions import save as save_subscriptions
from reader import board
from reader.board import Board, BoardError, Column
from reader.models import PaperRef
from store import cli, data
from store.firestore import (
    Conflict,
    Firestore,
    ServiceAccount,
    StoreError,
    decode_fields,
    encode_fields,
    signed_jwt,
)

TODAY = dt.date(2026, 11, 5)
NOW = dt.datetime(2026, 11, 5, 14, 0, tzinfo=dt.UTC)
DOCS = "https://firestore.googleapis.com/v1/projects/academia-test/databases/(default)/documents"
TOKEN = "https://oauth2.googleapis.com/token"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
ACCOUNT_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "academia-test",
        "client_email": "bot@academia-test.iam.gserviceaccount.com",
        "private_key": _PEM,
        "token_uri": TOKEN,
    }
)


def _ref(arxiv: str) -> PaperRef:
    ids = PaperIds(arxiv=arxiv)
    links = Links.model_validate({"abstract_page": f"https://arxiv.org/abs/{arxiv}"})
    return PaperRef(id=ids.canonical(), ids=ids, title=f"Paper {arxiv}", links=links)


def _db() -> Firestore:
    return Firestore(ServiceAccount.from_json(ACCOUNT_JSON))


def _doc(path: str, fields: dict[str, Any], update_time: str = "2026-11-05T10:00:00Z") -> Any:
    return {
        "name": f"projects/academia-test/databases/(default)/documents/{path}",
        "fields": encode_fields(fields),
        "updateTime": update_time,
    }


@pytest.fixture
def token() -> respx.Route:
    return respx.post(TOKEN).respond(json={"access_token": "tok", "expires_in": 3600})


def test_values_round_trip() -> None:
    value = {
        "s": "x",
        "i": 3,
        "f": 1.5,
        "b": True,
        "n": None,
        "l": [1, "a", {"k": []}],
        "m": {"nested": {"deep": "yes"}},
    }
    assert decode_fields(encode_fields(value)) == value
    assert encode_fields({"i": 7})["i"] == {"integerValue": "7"}


def test_jwt_is_signed_by_the_service_account() -> None:
    account = ServiceAccount.from_json(ACCOUNT_JSON)
    jwt = signed_jwt(account, 1_000_000)
    head, body, sig = jwt.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    assert claims["iss"] == account.client_email
    assert claims["aud"] == TOKEN
    assert claims["exp"] - claims["iat"] == 3600
    _KEY.public_key().verify(
        base64.urlsafe_b64decode(sig + "=="),
        f"{head}.{body}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_bad_service_account() -> None:
    with pytest.raises(StoreError, match="isn't a service account"):
        ServiceAccount.from_json('{"type": "nope"}')


@respx.mock
def test_get_set_and_token_reuse(token: respx.Route) -> None:
    respx.get(f"{DOCS}/boards/main").respond(json=_doc("boards/main", {"cards": []}))
    write = respx.patch(f"{DOCS}/boards/main").respond(json=_doc("boards/main", {}))
    db = _db()
    doc = db.get("boards/main")
    assert doc is not None and doc.data == {"cards": []}
    db.set("boards/main", {"cards": [], "schema_version": 1}, if_update_time=doc.update_time)
    request = write.calls.last.request
    assert request.url.params["currentDocument.updateTime"] == doc.update_time
    assert request.headers["Authorization"] == "Bearer tok"
    assert json.loads(request.content)["fields"]["schema_version"] == {"integerValue": "1"}
    assert token.call_count == 1


@respx.mock
def test_missing_doc_and_conflict(token: respx.Route) -> None:
    respx.get(f"{DOCS}/boards/main").respond(404)
    respx.patch(f"{DOCS}/boards/main").respond(
        400, json={"error": {"status": "FAILED_PRECONDITION"}}
    )
    db = _db()
    assert db.get("boards/main") is None
    with pytest.raises(Conflict):
        db.set("boards/main", {}, if_update_time="")


@respx.mock
def test_refused_token() -> None:
    respx.post(TOKEN).respond(401)
    with pytest.raises(StoreError, match="refused the service account"):
        _db().get("boards/main")


@respx.mock
def test_list_pages(token: respx.Route) -> None:
    respx.get(f"{DOCS}/devices").mock(
        side_effect=[
            httpx.Response(200, json={"documents": [_doc("devices/a", {})], "nextPageToken": "p2"}),
            httpx.Response(200, json={"documents": [_doc("devices/b", {})]}),
        ]
    )
    assert [d.id for d in _db().list("devices")] == ["a", "b"]


@respx.mock
def test_change_board_retries_once_after_the_site_saved(token: respx.Route) -> None:
    first = Board()
    first.add(_ref("2410.00001"), today=TODAY)
    respx.get(f"{DOCS}/boards/main").mock(
        side_effect=[
            httpx.Response(200, json=_doc("boards/main", {"schema_version": 1, "cards": []}, "t1")),
            httpx.Response(200, json=_doc("boards/main", first.model_dump(mode="json"), "t2")),
        ]
    )
    write = respx.patch(f"{DOCS}/boards/main").mock(
        side_effect=[
            httpx.Response(400, text="FAILED_PRECONDITION"),
            httpx.Response(200, json=_doc("boards/main", {})),
        ]
    )
    card = data.change_board(
        Path("unused"), lambda b: b.add(_ref("2410.00002"), today=TODAY), db=_db(), now=NOW
    )
    assert card.paper.id == "arxiv:2410.00002"
    saved = Board.model_validate(
        decode_fields(json.loads(write.calls.last.request.content)["fields"])
    )
    assert [c.paper.id for c in saved.cards] == ["arxiv:2410.00001", "arxiv:2410.00002"]
    assert write.calls.last.request.url.params["currentDocument.updateTime"] == "t2"


@respx.mock
def test_change_board_stops_on_a_rule(token: respx.Route) -> None:
    b = Board()
    b.add(_ref("2410.00001"), today=TODAY)
    respx.get(f"{DOCS}/boards/main").respond(json=_doc("boards/main", b.model_dump(mode="json")))
    write = respx.patch(f"{DOCS}/boards/main")
    with pytest.raises(BoardError):
        data.change_board(
            Path("unused"), lambda x: x.add(_ref("2410.00001"), today=TODAY), db=_db()
        )
    assert not write.called


@respx.mock
def test_load_board_raises_when_firestore_fails(token: respx.Route) -> None:
    respx.get(f"{DOCS}/boards/main").respond(503, text="unavailable")
    with pytest.raises(StoreError):
        data.load_board(Path("unused"), db=_db())


def test_local_fallback(tmp_path: Path) -> None:
    card = data.change_board(tmp_path, lambda b: b.add(_ref("2410.00001"), today=TODAY))
    assert data.load_board(tmp_path).find(card.paper.ids) is not None
    assert (tmp_path / "board.json").exists()
    sub = Subscription(endpoint="https://push.example/1", keys=Keys(p256dh="p", auth="a"))
    data.save_device(tmp_path, sub)
    assert [s.endpoint for s in data.load_devices(tmp_path)] == [sub.endpoint]
    data.forget_devices(tmp_path, [sub.endpoint])
    assert data.load_devices(tmp_path) == []


@respx.mock
def test_devices_in_firestore(token: respx.Route) -> None:
    endpoint = "https://push.example/abc"
    fields = {"endpoint": endpoint, "keys": {"p256dh": "p", "auth": "a"}, "device": "iPad"}
    respx.get(f"{DOCS}/devices").respond(json={"documents": [_doc("devices/x", fields)]})
    gone = respx.delete(f"{DOCS}/devices/{data.device_id(endpoint)}").respond(200, json={})
    db = _db()
    (sub,) = data.load_devices(Path("unused"), db)
    assert sub.device == "iPad"
    data.forget_devices(Path("unused"), [endpoint], db)
    assert gone.called


def test_device_id_matches_the_site() -> None:
    # The site uses SHA-256 of the endpoint, as lowercase hex (see push.js).
    assert data.device_id("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


@respx.mock
def test_setup_copies_files_and_allows_the_owner(
    token: respx.Route,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    b = Board()
    b.add(_ref("2410.00001"), today=TODAY)
    b.move(PaperIds(arxiv="2410.00001"), Column.DONE, today=TODAY)
    board.save(b, tmp_path / "board.json", now=NOW)
    sub = Subscription(endpoint="https://push.example/1", keys=Keys(p256dh="p", auth="a"))
    save_subscriptions(
        SubscriptionsFile(subscriptions=[sub]), tmp_path / "push" / "subscriptions.json", NOW
    )
    owner = respx.patch(f"{DOCS}/owners/abcdefghij1234567890").respond(json={})
    respx.get(f"{DOCS}/boards/main").respond(404)
    board_write = respx.patch(f"{DOCS}/boards/main").respond(json={})
    device = respx.patch(f"{DOCS}/devices/{data.device_id(sub.endpoint)}").respond(json={})
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", ACCOUNT_JSON)

    argv = ["setup", "--owner", "abcdefghij1234567890", "--data-dir", str(tmp_path)]
    assert cli.main(argv) == 0
    assert owner.called and device.called
    assert board_write.calls.last.request.url.params["currentDocument.exists"] == "false"
    out = capsys.readouterr().out
    assert "copied the board (1 cards)" in out
    assert "copied 1 notification device(s)" in out


def test_setup_needs_the_key_and_a_real_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["setup", "--owner", "x"]) == 1
    assert cli.main(["setup"]) == 1
    assert "FIREBASE_SERVICE_ACCOUNT is not set" in capsys.readouterr().err
