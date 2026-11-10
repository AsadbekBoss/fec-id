"""Auth building blocks without a database: hashing, policy, tokens, limiter, client IP."""

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from argon2 import PasswordHasher
from cryptography.hazmat.primitives import serialization
from starlette.requests import Request

from app.auth import passwords, tokens
from app.auth.client_ip import client_ip
from app.auth.permissions import ROLE_PERMISSIONS, Permission, permissions_for
from app.auth.rate_limit import SlidingWindowLimiter
from app.core.config import Settings, get_settings
from app.models.enums import UserRole

# --- passwords --------------------------------------------------------------------------------


async def test_hash_is_argon2id_and_verifies() -> None:
    h = await passwords.hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert await passwords.verify_password(h, "correct horse battery staple")
    assert not await passwords.verify_password(h, "correct horse battery stapl")
    assert not await passwords.verify_password("not-a-hash", "x")


async def test_unknown_user_path_never_succeeds() -> None:
    # Even the dummy password itself must not authenticate a missing user.
    assert not await passwords.verify_password(None, "dummy-password-used-for-constant-time-checks")


async def test_unicode_normalisation() -> None:
    h = await passwords.hash_password("Pássword-strong-1")  # combining accent
    assert await passwords.verify_password(h, "Pássword-strong-1")  # precomposed


def test_production_argon2_defaults() -> None:
    f = Settings.model_fields
    assert f["argon2_memory_kib"].default == 65536
    assert f["argon2_time_cost"].default == 3
    assert f["argon2_parallelism"].default == 4


def test_needs_rehash_when_parameters_change() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash("x" * 12)
    strong = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
    assert strong.check_needs_rehash(weak)


@pytest.mark.parametrize(
    ("pw", "expected"),
    [
        ("short", "at least 12"),
        ("x" * 129, "at most 128"),
        ("Password123!", "too common"),
        ("aaaaaaaaaaaaaa", "distinct"),
        ("john.smith-secret-2026", "username"),
    ],
)
def test_password_policy_rejections(pw: str, expected: str) -> None:
    problems = passwords.policy_violations(pw, "john.smith")
    assert any(expected in p for p in problems), problems


def test_password_policy_accepts_passphrase() -> None:
    assert passwords.policy_violations("tog'da qor yog'di 2026", "admin") == []


# --- tokens ---------------------------------------------------------------------------------


def _claims_token(**overrides: object) -> str:
    s = get_settings()
    keys = tokens.get_signing_keys()
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "role": "VIEWER",
        "typ": "access",
        "jti": "j",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    payload.update(overrides)
    return jwt.encode(
        payload, keys.private[keys.active_kid], algorithm="EdDSA", headers={"kid": keys.active_kid}
    )


def test_access_token_roundtrip() -> None:
    uid, sid = uuid.uuid4(), uuid.uuid4()
    token, ttl = tokens.issue_access_token(uid, sid, "ADMIN")
    assert ttl == get_settings().access_token_minutes * 60
    c = tokens.decode_access_token(token)
    assert (c.user_id, c.session_id, c.role) == (uid, sid, "ADMIN")
    assert jwt.get_unverified_header(token)["kid"] == "1"


def test_expired_token() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token, _ = tokens.issue_access_token(uuid.uuid4(), uuid.uuid4(), "VIEWER", now=past)
    with pytest.raises(tokens.TokenError) as e:
        tokens.decode_access_token(token)
    assert e.value.code == "TOKEN_EXPIRED"


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: "not.a.jwt",
        lambda: "",
        lambda: _claims_token(aud="someone-else"),
        lambda: _claims_token(iss="evil"),
        lambda: _claims_token(typ="refresh"),
        lambda: _claims_token(sub="not-a-uuid"),
        lambda: _claims_token(nbf=int((datetime.now(UTC) + timedelta(hours=1)).timestamp())),
    ],
    ids=["garbage", "empty", "audience", "issuer", "typ", "bad-sub", "not-yet-valid"],
)
def test_invalid_tokens(token_factory) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(tokens.TokenError) as e:
        tokens.decode_access_token(token_factory())
    assert e.value.code == "INVALID_TOKEN"


def test_missing_required_claim() -> None:
    keys = tokens.get_signing_keys()
    t = jwt.encode(
        {"sub": str(uuid.uuid4())}, keys.private["1"], algorithm="EdDSA", headers={"kid": "1"}
    )
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(t)


def test_alg_none_rejected() -> None:
    t = jwt.encode({"sub": "x"}, key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(t)


def test_hs256_key_confusion_rejected() -> None:
    """Classic attack: sign with HS256 using the *public* key as the HMAC secret."""
    pub = tokens.get_signing_keys().public("1")
    assert pub is not None
    secret = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    t = jwt.encode({"sub": "x"}, secret, algorithm="HS256", headers={"kid": "1"})
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(t)


def test_token_from_foreign_key_rejected() -> None:
    other = tokens.SigningKeys.parse(
        "1:" + base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="), 1
    )
    s = get_settings()
    now = int(datetime.now(UTC).timestamp())
    t = jwt.encode(
        {
            "iss": s.jwt_issuer,
            "aud": s.jwt_audience,
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "typ": "access",
            "jti": "j",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        },
        other.private["1"],
        algorithm="EdDSA",
        headers={"kid": "1"},
    )
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(t)


def test_unknown_kid_rejected() -> None:
    keys = tokens.get_signing_keys()
    s = get_settings()
    now = int(datetime.now(UTC).timestamp())
    t = jwt.encode(
        {
            "iss": s.jwt_issuer,
            "aud": s.jwt_audience,
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "typ": "access",
            "jti": "j",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        },
        keys.private["1"],
        algorithm="EdDSA",
        headers={"kid": "99"},
    )
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(t)


def test_signing_key_rotation() -> None:
    k1 = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    k2 = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    ring = tokens.SigningKeys.parse(f"1:{k1},2:{k2}", 2)
    assert ring.active_kid == "2"
    assert ring.public("1") is not None  # old tokens still verifiable
    with pytest.raises(ValueError):
        tokens.SigningKeys.parse(f"1:{k1}", 2)
    with pytest.raises(ValueError):
        tokens.SigningKeys.parse("1:" + base64.urlsafe_b64encode(b"short").decode(), 1)


def test_refresh_token_entropy_and_hash() -> None:
    raw, digest = tokens.new_refresh_token()
    assert len(raw) >= 43 and len(digest) == 32
    assert tokens.hash_refresh_token(raw) == digest
    assert tokens.new_refresh_token()[0] != raw


# --- permissions --------------------------------------------------------------------------


def test_role_hierarchy() -> None:
    viewer = permissions_for(UserRole.VIEWER)
    admin = permissions_for(UserRole.ADMIN)
    sa = permissions_for(UserRole.SUPER_ADMIN)
    assert viewer < admin < sa
    assert sa == frozenset(Permission)
    assert Permission.USERS_WRITE not in admin and Permission.SETTINGS_WRITE not in admin
    assert not any(p.value.endswith(":write") for p in viewer)
    assert set(ROLE_PERMISSIONS) == set(UserRole)


# --- rate limiter -------------------------------------------------------------------------


def test_sliding_window_limiter() -> None:
    lim = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert [lim.hit("ip", now=t)[0] for t in (0, 1, 2)] == [True, True, True]
    allowed, retry = lim.hit("ip", now=3)
    assert not allowed and 50 < retry <= 61
    assert lim.hit("other", now=3)[0]  # keys are independent
    assert lim.hit("ip", now=61)[0]  # window slid


def test_limiter_memory_bound() -> None:
    lim = SlidingWindowLimiter(limit=1, window_seconds=60, max_keys=100)
    for i in range(1000):
        lim.hit(f"k{i}", now=0)
    assert len(lim._hits) <= 101


# --- client IP ----------------------------------------------------------------------------


def _req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_xff_ignored_from_untrusted_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxies", [])
    assert client_ip(_req("203.0.113.5", "1.2.3.4")) == "203.0.113.5"


def test_xff_used_behind_trusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxies", ["10.0.0.1"])
    assert client_ip(_req("10.0.0.1", "1.2.3.4, 198.51.100.7")) == "198.51.100.7"
    assert client_ip(_req("10.0.0.1", "garbage")) == "10.0.0.1"
    assert client_ip(_req("10.0.0.1")) == "10.0.0.1"
