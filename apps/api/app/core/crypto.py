"""Application-level encryption for sensitive columns (ARCHITECTURE §8, DEVELOPMENT_RULES §6.3).

* AES-256-GCM with random 96-bit nonces.
* Per-purpose subkeys derived with HKDF-SHA256 from a master key, so a biometric key is never
  the same as a credential key (domain separation).
* Ciphertext layout:  key_id (2 bytes, big-endian) || nonce (12) || ciphertext || tag (16)
* Associated data (AAD) binds each ciphertext to its row and column. A ciphertext copied into
  another row (e.g. another employee's embedding) fails to decrypt.
"""

from __future__ import annotations

import base64
import os
import struct
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

NONCE_LEN = 12
TAG_LEN = 16
HEADER_LEN = 2 + NONCE_LEN
MIN_CIPHERTEXT_LEN = HEADER_LEN + TAG_LEN


class Purpose(StrEnum):
    BIOMETRIC = "biometric-template"
    CREDENTIAL = "device-credential"
    MFA = "mfa-secret"
    SNAPSHOT = "snapshot"
    PROFILE_PHOTO = "profile-photo"
    CONSENT_DOCUMENT = "consent-document"


class DecryptionError(Exception):
    """Raised for unknown key, tampered data or wrong AAD. Never includes plaintext."""


@dataclass(frozen=True)
class Keyring:
    keys: dict[int, bytes]
    active_key_id: int

    @classmethod
    def parse(cls, spec: str, active_key_id: int) -> Keyring:
        keys: dict[int, bytes] = {}
        for item in filter(None, (p.strip() for p in spec.split(","))):
            kid_s, _, b64 = item.partition(":")
            kid = int(kid_s)
            raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
            if len(raw) != 32:
                raise ValueError(f"Encryption key {kid} must be 32 bytes")
            if not 0 < kid < 2**16:
                raise ValueError("Key id must be 1..65535")
            keys[kid] = raw
        if active_key_id not in keys:
            raise ValueError("Active encryption key id is not in the keyring")
        return cls(keys=keys, active_key_id=active_key_id)

    def subkey(self, key_id: int, purpose: Purpose) -> bytes:
        try:
            master = self.keys[key_id]
        except KeyError:
            raise DecryptionError(f"Unknown key id {key_id}") from None
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.value.encode()
        ).derive(master)


@lru_cache
def get_keyring() -> Keyring:
    s = get_settings()
    return Keyring.parse(s.data_encryption_keys.get_secret_value(), s.data_encryption_active_key_id)


def generate_master_key() -> str:
    """Helper for setup scripts: returns a base64url 32-byte key."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def encrypt(plaintext: bytes, purpose: Purpose, aad: str, keyring: Keyring | None = None) -> bytes:
    kr = keyring or get_keyring()
    kid = kr.active_key_id
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(kr.subkey(kid, purpose)).encrypt(nonce, plaintext, aad.encode())
    return struct.pack(">H", kid) + nonce + ct


def decrypt(blob: bytes, purpose: Purpose, aad: str, keyring: Keyring | None = None) -> bytes:
    if len(blob) < MIN_CIPHERTEXT_LEN:
        raise DecryptionError("Ciphertext too short")
    kr = keyring or get_keyring()
    (kid,) = struct.unpack(">H", blob[:2])
    nonce = blob[2:HEADER_LEN]
    try:
        return AESGCM(kr.subkey(kid, purpose)).decrypt(nonce, blob[HEADER_LEN:], aad.encode())
    except InvalidTag:
        raise DecryptionError("Ciphertext authentication failed") from None


def key_id_of(blob: bytes) -> int:
    return int(struct.unpack(">H", blob[:2])[0])


# --- float32 vector packing for embeddings ------------------------------------------------


def pack_vector(values: list[float]) -> bytes:
    """Little-endian float32, independent of host byte order."""
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(data: bytes) -> list[float]:
    if len(data) % 4:
        raise ValueError("Vector byte length must be a multiple of 4")
    return list(struct.unpack(f"<{len(data) // 4}f", data))
