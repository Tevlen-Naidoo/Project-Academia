"""Encrypt and send one Web Push message (RFC 8291, aes128gcm)."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from notifier import vapid
from notifier.subscriptions import Subscription

RECORD_SIZE = 4096
TTL = 12 * 3600
"""Seconds the push service keeps a message for a device that is offline."""


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    server_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The request body for `payload`, readable only by the subscribed browser."""
    ua_public = vapid.b64url_decode(p256dh)
    auth_secret = vapid.b64url_decode(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = server_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)

    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    if len(payload) + 1 + 16 > RECORD_SIZE - 86:
        raise ValueError("Push payload is too long.")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


@dataclass(frozen=True, slots=True)
class Result:
    subscription: Subscription
    status: int
    detail: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def gone(self) -> bool:
        """The device unsubscribed or the subscription expired: drop it."""
        return self.status in (404, 410)


def send(
    client: httpx.Client,
    subscription: Subscription,
    payload: bytes,
    key: ec.EllipticCurvePrivateKey,
    subject: str,
) -> Result:
    body = encrypt(payload, subscription.keys.p256dh, subscription.keys.auth)
    headers = {
        "Authorization": vapid.authorization(key, subscription.endpoint, subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL),
        "Urgency": "normal",
    }
    try:
        response = client.post(subscription.endpoint, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return Result(subscription, 0, str(exc))
    detail = "" if response.is_success else response.text[:200]
    return Result(subscription, response.status_code, detail)
