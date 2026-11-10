"""Argon2id password hashing and password policy.

* Hashing and verification are CPU- and memory-heavy by design, so they run in a worker thread
  and never block the event loop.
* Unknown usernames are verified against a dummy hash, so response time does not reveal
  whether an account exists.
* Passwords are NFKC-normalised, so visually identical Unicode input hashes the same.
"""

from __future__ import annotations

import threading
import unicodedata
from functools import lru_cache

import anyio
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

MIN_LENGTH = 12
MAX_LENGTH = 128  # bounds hashing cost per request

# Small built-in blocklist of very common choices (extend with a larger list if desired).
_COMMON = {
    "password",
    "password1",
    "password123",
    "passw0rd",
    "qwerty",
    "qwerty123",
    "qwertyuiop",
    "123456",
    "12345678",
    "123456789",
    "1234567890",
    "111111",
    "000000",
    "abc123",
    "admin",
    "administrator",
    "letmein",
    "welcome",
    "iloveyou",
    "monkey",
    "dragon",
    "football",
    "parol",
    "parol123",
    "1q2w3e4r",
    "1q2w3e4r5t",
    "zaq12wsx",
    "changeme",
    "attendance",
}


def normalize(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


@lru_cache
def get_hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_kib,
        parallelism=s.argon2_parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


@lru_cache
def _dummy_hash() -> str:
    return get_hasher().hash("dummy-password-used-for-constant-time-checks")


# At most N hashes run at once: each Argon2 call allocates `argon2_memory_kib`. Without a cap,
# a login flood could make all worker threads hash at the same time (40 x 64 MiB).
_HASH_SLOTS = threading.BoundedSemaphore(4)


def hash_password_sync(password: str) -> str:
    with _HASH_SLOTS:
        return get_hasher().hash(normalize(password))


async def hash_password(password: str) -> str:
    return await anyio.to_thread.run_sync(hash_password_sync, password)


def _verify_sync(password_hash: str | None, password: str) -> bool:
    with _HASH_SLOTS:
        try:
            ok = get_hasher().verify(password_hash or _dummy_hash(), normalize(password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    return ok and password_hash is not None


async def verify_password(password_hash: str | None, password: str) -> bool:
    """`password_hash=None` (unknown user) still performs a full verification, then fails."""
    return await anyio.to_thread.run_sync(_verify_sync, password_hash, password)


def needs_rehash(password_hash: str) -> bool:
    try:
        return get_hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def policy_violations(password: str, username: str | None = None) -> list[str]:
    """NIST SP 800-63B style: length + blocklist, no composition rules."""
    p = normalize(password)
    problems: list[str] = []
    if len(p) < MIN_LENGTH:
        problems.append(f"Password must be at least {MIN_LENGTH} characters.")
    if len(p) > MAX_LENGTH:
        problems.append(f"Password must be at most {MAX_LENGTH} characters.")
    lowered = p.lower()
    if lowered in _COMMON or lowered.rstrip("0123456789!") in _COMMON:
        problems.append("Password is too common.")
    if len(set(p)) < 4:
        problems.append("Password has too few distinct characters.")
    if username and len(username) >= 3 and username.lower() in lowered:
        problems.append("Password must not contain the username.")
    return problems
