"""A small Firestore client over its REST API, signed in with a service account.

Docs: https://firebase.google.com/docs/firestore/use-rest-api
Requests made with a service account's OAuth token skip the security rules, so
this is only for the daily run and the command line, never for the site.

The key is the service account JSON file from the Firebase console, passed in
the FIREBASE_SERVICE_ACCOUNT environment variable (an Actions secret).
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ENV_VAR = "FIREBASE_SERVICE_ACCOUNT"
SCOPE = "https://www.googleapis.com/auth/datastore"
API = "https://firestore.googleapis.com/v1"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class StoreError(RuntimeError):
    """Firestore could not be reached or refused a request."""


class Conflict(StoreError):
    """The document changed since it was read."""


@dataclass(frozen=True, slots=True)
class ServiceAccount:
    project_id: str
    client_email: str
    private_key: str
    token_uri: str = TOKEN_URI

    @classmethod
    def from_json(cls, text: str) -> ServiceAccount:
        try:
            info = json.loads(text)
            return cls(
                project_id=info["project_id"],
                client_email=info["client_email"],
                private_key=info["private_key"],
                token_uri=info.get("token_uri") or TOKEN_URI,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreError(f"{ENV_VAR} isn't a service account key file ({exc})") from exc

    @classmethod
    def from_env(cls) -> ServiceAccount | None:
        text = os.environ.get(ENV_VAR, "")
        return cls.from_json(text) if text.strip() else None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def signed_jwt(account: ServiceAccount, now: float) -> str:
    """The assertion Google's token endpoint exchanges for an access token."""
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": account.client_email,
        "scope": SCOPE,
        "aud": account.token_uri,
        "iat": int(now),
        "exp": int(now) + 3600,
    }
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
    )
    key = serialization.load_pem_private_key(account.private_key.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise StoreError("the service account key is not an RSA key")
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


# --- Firestore's typed values <-> plain JSON ------------------------------------


def encode(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list | tuple):
        return {"arrayValue": {"values": [encode(v) for v in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": encode_fields(value)}}
    raise TypeError(f"can't store {type(value).__name__} in Firestore")


def encode_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {str(k): encode(v) for k, v in data.items()}


def decode(value: dict[str, Any]) -> Any:
    (kind, raw), *_ = value.items()
    if kind == "nullValue":
        return None
    if kind == "integerValue":
        return int(raw)
    if kind in {"booleanValue", "doubleValue", "stringValue", "timestampValue"}:
        return raw
    if kind == "arrayValue":
        return [decode(v) for v in (raw or {}).get("values", [])]
    if kind == "mapValue":
        return decode_fields((raw or {}).get("fields", {}))
    raise StoreError(f"unexpected Firestore value type {kind}")


def decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: decode(v) for k, v in fields.items()}


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    data: dict[str, Any]
    update_time: str


class Firestore:
    def __init__(self, account: ServiceAccount, client: httpx.Client | None = None) -> None:
        self.account = account
        self._client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        self._token: str | None = None
        self._token_expires = 0.0

    @property
    def _root(self) -> str:
        return f"{API}/projects/{self.account.project_id}/databases/(default)/documents"

    def _url(self, path: str) -> str:
        return f"{self._root}/{'/'.join(quote(p, safe='') for p in path.split('/'))}"

    def _headers(self) -> dict[str, str]:
        now = time.time()
        if self._token is None or now > self._token_expires - 60:
            response = self._client.post(
                self.account.token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt(self.account, now),
                },
            )
            if response.status_code != 200:
                raise StoreError(f"Google refused the service account ({response.status_code})")
            body = response.json()
            self._token = str(body["access_token"])
            self._token_expires = now + float(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _check(self, response: httpx.Response, what: str) -> None:
        if response.status_code == 409 or (
            response.status_code == 400 and "FAILED_PRECONDITION" in response.text
        ):
            raise Conflict(f"{what}: changed by someone else")
        if response.status_code >= 400:
            raise StoreError(f"{what}: Firestore said {response.status_code} {response.text[:200]}")

    @staticmethod
    def _document(body: dict[str, Any]) -> Document:
        return Document(
            id=str(body["name"]).rsplit("/", 1)[-1],
            data=decode_fields(body.get("fields", {})),
            update_time=str(body.get("updateTime", "")),
        )

    def get(self, path: str) -> Document | None:
        try:
            response = self._client.get(self._url(path), headers=self._headers())
        except httpx.HTTPError as exc:
            raise StoreError(f"read {path}: {exc}") from exc
        if response.status_code == 404:
            return None
        self._check(response, f"read {path}")
        return self._document(response.json())

    def set(self, path: str, data: dict[str, Any], *, if_update_time: str | None = None) -> None:
        """Write the whole document. With `if_update_time` ("" = must not exist yet),
        fail with `Conflict` if it changed since that read."""
        params: dict[str, str] = {}
        if if_update_time == "":
            params["currentDocument.exists"] = "false"
        elif if_update_time:
            params["currentDocument.updateTime"] = if_update_time
        try:
            response = self._client.patch(
                self._url(path),
                params=params,
                headers=self._headers(),
                json={"fields": encode_fields(data)},
            )
        except httpx.HTTPError as exc:
            raise StoreError(f"write {path}: {exc}") from exc
        self._check(response, f"write {path}")

    def delete(self, path: str) -> None:
        try:
            response = self._client.delete(self._url(path), headers=self._headers())
        except httpx.HTTPError as exc:
            raise StoreError(f"delete {path}: {exc}") from exc
        if response.status_code != 404:
            self._check(response, f"delete {path}")

    def list(self, collection: str) -> Iterator[Document]:
        token = ""
        while True:
            params = {"pageSize": "300", **({"pageToken": token} if token else {})}
            try:
                response = self._client.get(
                    self._url(collection), params=params, headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise StoreError(f"list {collection}: {exc}") from exc
            self._check(response, f"list {collection}")
            body = response.json()
            for doc in body.get("documents", []):
                yield self._document(doc)
            token = body.get("nextPageToken", "")
            if not token:
                return

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Firestore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
