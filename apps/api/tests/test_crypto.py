"""Unit tests for the encryption layer (no database needed)."""

import base64
import os

import pytest

from app.core import crypto


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def test_roundtrip_and_layout() -> None:
    kr = crypto.Keyring.parse(f"1:{_key()}", 1)
    blob = crypto.encrypt(b"secret", crypto.Purpose.CREDENTIAL, "cameras:1:password", kr)
    assert len(blob) == crypto.HEADER_LEN + len(b"secret") + crypto.TAG_LEN
    assert crypto.key_id_of(blob) == 1
    assert crypto.decrypt(blob, crypto.Purpose.CREDENTIAL, "cameras:1:password", kr) == b"secret"


def test_nonce_is_random() -> None:
    kr = crypto.Keyring.parse(f"1:{_key()}", 1)
    a = crypto.encrypt(b"x", crypto.Purpose.MFA, "aad", kr)
    b = crypto.encrypt(b"x", crypto.Purpose.MFA, "aad", kr)
    assert a != b


@pytest.mark.parametrize(
    "mutate",
    [
        lambda blob, aad, p: (blob, aad + "x", p),  # wrong row binding
        lambda blob, aad, p: (blob, aad, crypto.Purpose.MFA),  # wrong purpose/subkey
        lambda blob, aad, p: (blob[:-1] + bytes([blob[-1] ^ 1]), aad, p),  # tampered tag
        lambda blob, aad, p: (blob[:20], aad, p),  # truncated
    ],
)
def test_decrypt_failures(mutate) -> None:  # type: ignore[no-untyped-def]
    kr = crypto.Keyring.parse(f"1:{_key()}", 1)
    aad, p = "face_embeddings:1", crypto.Purpose.BIOMETRIC
    blob = crypto.encrypt(b"vector-bytes", p, aad, kr)
    b2, aad2, p2 = mutate(blob, aad, p)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(b2, p2, aad2, kr)


def test_key_rotation() -> None:
    k1, k2 = _key(), _key()
    old = crypto.Keyring.parse(f"1:{k1}", 1)
    blob = crypto.encrypt(b"v", crypto.Purpose.BIOMETRIC, "a", old)
    new = crypto.Keyring.parse(f"1:{k1},2:{k2}", 2)
    assert crypto.decrypt(blob, crypto.Purpose.BIOMETRIC, "a", new) == b"v"  # old still readable
    assert crypto.key_id_of(crypto.encrypt(b"v", crypto.Purpose.BIOMETRIC, "a", new)) == 2
    retired = crypto.Keyring.parse(f"2:{k2}", 2)
    with pytest.raises(crypto.DecryptionError, match="Unknown key id"):
        crypto.decrypt(blob, crypto.Purpose.BIOMETRIC, "a", retired)


@pytest.mark.parametrize(
    ("spec", "active"),
    [("1:" + base64.urlsafe_b64encode(b"short").decode(), 1), (f"1:{_key()}", 2), ("", 1)],
)
def test_keyring_validation(spec: str, active: int) -> None:
    with pytest.raises(ValueError):
        crypto.Keyring.parse(spec, active)


def test_vector_packing_is_little_endian_float32() -> None:
    data = crypto.pack_vector([1.0, -2.5])
    assert data == b"\x00\x00\x80\x3f\x00\x00\x20\xc0"
    assert crypto.unpack_vector(data) == [1.0, -2.5]
