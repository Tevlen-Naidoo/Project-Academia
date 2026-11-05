"""VAPID keys (RFC 8292): the key pair that proves pushes come from this project.

The private key lives in the `VAPID_PRIVATE_KEY` Actions secret. It may be the
base64url string `python -m notifier keys` prints, or a PEM file from
`openssl ecparam -name prime256v1 -genkey -noout`. The public key is derived from
it, so there is only one thing to keep.
"""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ENV_VAR = "VAPID_PRIVATE_KEY"
TOKEN_LIFETIME = 12 * 3600
"""Push services refuse tokens that live longer than 24 hours."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate() -> str:
    """A new private key, as base64url of the 32-byte P-256 scalar."""
    key = ec.generate_private_key(ec.SECP256R1())
    return b64url(key.private_numbers().private_value.to_bytes(32, "big"))


def load_private_key(text: str) -> ec.EllipticCurvePrivateKey:
    text = text.strip()
    if not text:
        raise ValueError("The VAPID private key is empty.")
    if "-----BEGIN" in text:
        key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
    else:
        raw = b64url_decode(text)
        if len(raw) == 32:
            return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
        key = serialization.load_der_private_key(raw, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("The VAPID private key must be a P-256 (prime256v1) key.")
    return key


def public_key(key: ec.EllipticCurvePrivateKey) -> str:
    """The application server key the browser subscribes with (base64url, uncompressed)."""
    point = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64url(point)


def token(
    key: ec.EllipticCurvePrivateKey, endpoint: str, subject: str, now: float | None = None
) -> str:
    """A signed ES256 JWT for the push service that owns `endpoint`."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now if now is not None else time.time()) + TOKEN_LIFETIME),
        "sub": subject,
    }
    header = {"typ": "JWT", "alg": "ES256"}
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    return signing_input + "." + b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def authorization(
    key: ec.EllipticCurvePrivateKey, endpoint: str, subject: str, now: float | None = None
) -> str:
    return f"vapid t={token(key, endpoint, subject, now)}, k={public_key(key)}"
