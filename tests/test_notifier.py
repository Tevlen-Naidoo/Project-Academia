from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from notifier import cli, subscriptions, vapid, webpush
from scorer.models import PicksFile
from tests.test_site import DAY2, _picks

APPLE = "https://web.push.apple.com/QGuQyavXutnMH"
GOOGLE = "https://fcm.googleapis.com/fcm/send/abc"


class Device:
    """A browser's side of a push subscription, able to read what we send it."""

    def __init__(self, endpoint: str) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.subscription = subscriptions.Subscription(
            endpoint=endpoint,
            keys=subscriptions.Keys(p256dh=vapid.b64url(public), auth=vapid.b64url(self.auth)),
            device="iPhone",
        )

    def decrypt(self, body: bytes) -> bytes:
        salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        assert rs == 4096
        as_public = body[21 : 21 + idlen]
        ua_public = vapid.b64url_decode(self.subscription.keys.p256dh)
        server = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
        shared = self.key.exchange(ec.ECDH(), server)

        def mac(k: bytes, d: bytes) -> bytes:
            return hmac.new(k, d, hashlib.sha256).digest()

        ikm = mac(mac(self.auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
        prk = mac(salt, ikm)
        cek = mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
        nonce = mac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen :], None)
        assert plain.endswith(b"\x02")
        return plain[:-1]


def test_encrypted_payload_reads_back() -> None:
    device = Device(APPLE)
    keys = device.subscription.keys
    body = webpush.encrypt(b'{"title":"hi"}', keys.p256dh, keys.auth)
    assert device.decrypt(body) == b'{"title":"hi"}'


def test_key_formats_give_the_same_public_key() -> None:
    raw = vapid.generate()
    key = vapid.load_private_key(raw)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    der = key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = vapid.public_key(key)
    assert len(vapid.b64url_decode(public)) == 65
    assert vapid.public_key(vapid.load_private_key(pem)) == public
    assert vapid.public_key(vapid.load_private_key(vapid.b64url(der))) == public
    with pytest.raises(ValueError):
        vapid.load_private_key("  ")


def test_token_is_signed_for_the_push_service() -> None:
    key = vapid.load_private_key(vapid.generate())
    token = vapid.token(key, APPLE, "https://example.github.io/site/", now=1_000)
    header, claims, signature = token.split(".")
    assert json.loads(vapid.b64url_decode(claims)) == {
        "aud": "https://web.push.apple.com",
        "exp": 1_000 + vapid.TOKEN_LIFETIME,
        "sub": "https://example.github.io/site/",
    }
    raw = vapid.b64url_decode(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_daily_message(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _picks(data, DAY2)
    picks = PicksFile.model_validate_json((data / "picks" / f"{DAY2}.json").read_bytes())
    message = cli.daily_message(picks, DAY2)
    assert message["title"] == "3 new papers for today"
    assert message["body"].count("• ") == 3
    assert message["date"] == DAY2.isoformat()
    later = cli.daily_message(picks, DAY2 + dt.timedelta(days=1))
    assert later["title"] == "3 new papers for Thursday 5 November"


def test_site_url() -> None:
    assert cli.site_url("Tevlen-Naidoo/Project-Academia") == (
        "https://tevlen-naidoo.github.io/Project-Academia/"
    )


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[Device]]:
    data = tmp_path / "data"
    _picks(data, DAY2)
    devices = [Device(APPLE), Device(GOOGLE)]
    file = subscriptions.SubscriptionsFile(subscriptions=[d.subscription for d in devices])
    subscriptions.save(file, data / "push" / "subscriptions.json", dt.datetime.now(dt.UTC))
    monkeypatch.setenv(vapid.ENV_VAR, vapid.generate())
    return data, devices


@respx.mock
def test_send_pushes_to_every_device_and_forgets_gone_ones(
    setup: tuple[Path, list[Device]], capsys: pytest.CaptureFixture[str]
) -> None:
    data, (apple, _) = setup
    apple_route = respx.post(APPLE).mock(return_value=httpx.Response(201))
    google_route = respx.post(GOOGLE).mock(return_value=httpx.Response(410))

    code = cli.main(["send", "--date", DAY2.isoformat(), "--data-dir", str(data), "--repo", "o/r"])
    assert code == 0

    request = apple_route.calls.last.request
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert request.headers["Authorization"].startswith("vapid t=")
    message = json.loads(apple.decrypt(request.content))
    assert message["date"] == DAY2.isoformat()
    assert message["body"].count("• ") == 3
    assert google_route.called

    left = subscriptions.load(data / "push" / "subscriptions.json")
    assert [s.endpoint for s in left.subscriptions] == [APPLE]
    assert "Sent to 1 of 2 devices." in capsys.readouterr().out


@respx.mock
def test_send_fails_when_no_device_gets_it(setup: tuple[Path, list[Device]]) -> None:
    data, _ = setup
    respx.post(APPLE).mock(return_value=httpx.Response(403, text="BadJwtToken"))
    respx.post(GOOGLE).mock(return_value=httpx.Response(500))
    assert cli.main(["send", "--test", "--data-dir", str(data)]) == 1
    left = subscriptions.load(data / "push" / "subscriptions.json")
    assert len(left.subscriptions) == 2


def test_send_skips_quietly_without_key_or_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(vapid.ENV_VAR, raising=False)
    assert cli.main(["send", "--data-dir", str(tmp_path)]) == 0
    assert "not set" in capsys.readouterr().out
    monkeypatch.setenv(vapid.ENV_VAR, vapid.generate())
    assert cli.main(["send", "--data-dir", str(tmp_path)]) == 0
    assert "No devices" in capsys.readouterr().out


def test_keys_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["keys"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line and " " not in line]
    private, public = lines
    assert vapid.public_key(vapid.load_private_key(private)) == public


def _code(sub: subscriptions.Subscription) -> str:
    body = json.dumps({"endpoint": sub.endpoint, "keys": sub.keys.model_dump(), "device": "iPad"})
    return "pa1." + vapid.b64url(body.encode())


@respx.mock
def test_add_device_saves_it_and_sends_a_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(vapid.ENV_VAR, vapid.generate())
    device = Device(APPLE)
    route = respx.post(APPLE).mock(return_value=httpx.Response(201))
    code = _code(device.subscription)
    # Pasting can add line breaks; they don't matter.
    pasted = code[:40] + "\n " + code[40:]
    assert cli.main(["add-device", pasted, "--data-dir", str(tmp_path)]) == 0
    assert cli.main(["add-device", code, "--data-dir", str(tmp_path)]) == 0

    saved = subscriptions.load(tmp_path / "push" / "subscriptions.json").subscriptions
    assert [(s.endpoint, s.device) for s in saved] == [(APPLE, "iPad")]
    assert saved[0].added_on is not None
    assert json.loads(device.decrypt(route.calls.last.request.content))["tag"] == "test"
    assert "Sent a test notification" in capsys.readouterr().out


def test_add_device_rejects_a_bad_code(tmp_path: Path) -> None:
    for code in ["hello", "pa1.!!!", "pa1." + vapid.b64url(b'{"endpoint": "http://x"}')]:
        assert cli.main(["add-device", code, "--data-dir", str(tmp_path)]) == 1
    assert not (tmp_path / "push" / "subscriptions.json").exists()
