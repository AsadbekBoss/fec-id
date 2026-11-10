"""Template encryption, byte-compatible with the Core API (apps/api/app/core/crypto.py).

AES-256-GCM, HKDF-SHA256 subkey with info "biometric-template", layout
key_id(2B) || nonce(12B) || ciphertext || tag(16B). Vectors are little-endian float32.
Interoperability is covered by a fixed test vector produced by the Core API.
"""

from __future__ import annotations

import base64
import os
import struct
from dataclasses import dataclass

import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

BIOMETRIC_INFO = b"biometric-template"
NONCE_LEN, TAG_LEN = 12, 16
HEADER_LEN = 2 + NONCE_LEN


class DecryptionError(Exception):
    pass


@dataclass(frozen=True)
class Keyring:
    keys: dict[int, bytes]
    active_key_id: int

    @classmethod
    def parse(cls, spec: str, active_key_id: int) -> Keyring:
        keys: dict[int, bytes] = {}
        for item in filter(None, (p.strip() for p in spec.split(","))):
            kid_s, _, b64 = item.partition(":")
            raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
            if len(raw) != 32:
                raise ValueError(f"Encryption key {kid_s} must be 32 bytes")
            keys[int(kid_s)] = raw
        if active_key_id not in keys:
            raise ValueError("Active key id not in keyring")
        return cls(keys, active_key_id)

    def subkey(self, kid: int) -> bytes:
        try:
            master = self.keys[kid]
        except KeyError:
            raise DecryptionError(f"Unknown key id {kid}") from None
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=BIOMETRIC_INFO).derive(master)


def encrypt_vector(vec: np.ndarray, aad: str, keyring: Keyring) -> bytes:
    data = np.asarray(vec, dtype="<f4").tobytes()
    nonce = os.urandom(NONCE_LEN)
    kid = keyring.active_key_id
    return struct.pack(">H", kid) + nonce + AESGCM(keyring.subkey(kid)).encrypt(nonce, data, aad.encode())


def decrypt_vector(blob: bytes, aad: str, keyring: Keyring) -> np.ndarray:
    if len(blob) < HEADER_LEN + TAG_LEN:
        raise DecryptionError("Ciphertext too short")
    (kid,) = struct.unpack(">H", blob[:2])
    try:
        data = AESGCM(keyring.subkey(kid)).decrypt(blob[2:HEADER_LEN], blob[HEADER_LEN:], aad.encode())
    except InvalidTag:
        raise DecryptionError("Template authentication failed") from None
    return np.frombuffer(data, dtype="<f4").astype(np.float32)
