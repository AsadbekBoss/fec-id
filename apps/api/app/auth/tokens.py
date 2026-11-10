"""Access tokens (JWT, EdDSA/Ed25519) and opaque refresh tokens.

* Asymmetric signing: other services (e.g. recognition) can verify tokens with the public key
  only, without being able to mint them.
* Strict validation: algorithm pinned to EdDSA (no "none", no HS/RS confusion), issuer,
  audience, exp/nbf/iat required, `typ` must be "access", `kid` must be known.
* Refresh tokens are 256-bit random strings. Only their SHA-256 is stored.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.core.config import get_settings

ALGORITHM = "EdDSA"
TOKEN_TYPE = "access"  # noqa: S105 (JWT typ claim, not a secret)


class TokenError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # TOKEN_EXPIRED | INVALID_TOKEN


@dataclass(frozen=True)
class SigningKeys:
    private: dict[str, Ed25519PrivateKey]
    active_kid: str

    @classmethod
    def parse(cls, spec: str, active_key_id: int) -> SigningKeys:
        keys: dict[str, Ed25519PrivateKey] = {}
        for item in filter(None, (p.strip() for p in spec.split(","))):
            kid, _, b64 = item.partition(":")
            seed = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
            if len(seed) != 32:
                raise ValueError(f"JWT signing key {kid} must be a 32-byte Ed25519 seed")
            keys[str(int(kid))] = Ed25519PrivateKey.from_private_bytes(seed)
        if str(active_key_id) not in keys:
            raise ValueError("Active JWT key id is not in JWT_SIGNING_KEYS")
        return cls(private=keys, active_kid=str(active_key_id))

    def public(self, kid: str) -> Ed25519PublicKey | None:
        k = self.private.get(kid)
        return k.public_key() if k else None


@lru_cache
def get_signing_keys() -> SigningKeys:
    s = get_settings()
    return SigningKeys.parse(s.jwt_signing_keys.get_secret_value(), s.jwt_active_key_id)


def generate_signing_seed() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    role: str
    issued_at: datetime
    expires_at: datetime
    jti: str


def issue_access_token(
    user_id: uuid.UUID, session_id: uuid.UUID, role: str, now: datetime | None = None
) -> tuple[str, int]:
    s = get_settings()
    keys = get_signing_keys()
    now = now or datetime.now(UTC)
    ttl = timedelta(minutes=s.access_token_minutes)
    payload = {
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role,  # informational; authorization always uses the role from the DB
        "typ": TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    token = jwt.encode(
        payload,
        keys.private[keys.active_kid],
        algorithm=ALGORITHM,
        headers={"kid": keys.active_kid},
    )
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> AccessClaims:
    s = get_settings()
    keys = get_signing_keys()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != ALGORITHM:
            raise TokenError("INVALID_TOKEN")
        public = keys.public(str(header.get("kid", "")))
        if public is None:
            raise TokenError("INVALID_TOKEN")
        data = jwt.decode(
            token,
            public,
            algorithms=[ALGORITHM],
            audience=s.jwt_audience,
            issuer=s.jwt_issuer,
            leeway=s.jwt_leeway_seconds,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "sid", "jti"]},
        )
        if data.get("typ") != TOKEN_TYPE:
            raise TokenError("INVALID_TOKEN")
        return AccessClaims(
            user_id=uuid.UUID(data["sub"]),
            session_id=uuid.UUID(data["sid"]),
            role=str(data.get("role", "")),
            issued_at=datetime.fromtimestamp(data["iat"], UTC),
            expires_at=datetime.fromtimestamp(data["exp"], UTC),
            jti=str(data["jti"]),
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("TOKEN_EXPIRED") from None
    except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
        raise TokenError("INVALID_TOKEN") from None


# --- refresh tokens -----------------------------------------------------------------------


def new_refresh_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
