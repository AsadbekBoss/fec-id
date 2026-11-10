import base64
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from recognition import gallery_store
from recognition.crypto import DecryptionError, Keyring, decrypt_vector, encrypt_vector

from .conftest import TEST_KEY, unit

# Produced by apps/api (app.core.crypto.encrypt, Purpose.BIOMETRIC, key id 7, key bytes 0..31,
# AAD "face_templates:abc", vector [0.25, -0.5, 1.0, 0.125]).
CORE_API_BLOB = "AAfVbr0rLWa4Mat1LDvMQrDNbqfGNAC9J/u+IfZnzQlde9BrTFW+Wfws/xa8ow=="


def test_decrypts_core_api_ciphertext(keyring: Keyring) -> None:
    vec = decrypt_vector(base64.b64decode(CORE_API_BLOB), "face_templates:abc", keyring)
    assert vec.tolist() == [0.25, -0.5, 1.0, 0.125]


def test_round_trip_and_aad_binding(keyring: Keyring) -> None:
    v = unit([1, 2, 3, 4])
    blob = encrypt_vector(v, "a", keyring)
    assert blob[:2] == b"\x00\x07"
    assert np.allclose(decrypt_vector(blob, "a", keyring), v)
    with pytest.raises(DecryptionError):
        decrypt_vector(blob, "b", keyring)


def test_tamper_unknown_key_and_short_blob(keyring: Keyring) -> None:
    blob = bytearray(encrypt_vector(unit([1, 0, 0, 0]), "a", keyring))
    blob[-1] ^= 1
    with pytest.raises(DecryptionError):
        decrypt_vector(bytes(blob), "a", keyring)
    other = Keyring.parse(f"8:{TEST_KEY}", 8)
    with pytest.raises(DecryptionError):
        decrypt_vector(encrypt_vector(unit([1, 0, 0, 0]), "a", other), "a", keyring)
    with pytest.raises(DecryptionError):
        decrypt_vector(b"\x00\x07short", "a", keyring)


def test_gallery_file_round_trip_has_no_plaintext(tmp_path: Path, keyring: Keyring) -> None:
    v = unit([0.1, 0.2, 0.3, 0.4])
    e = gallery_store.make_entry(v, "subj-01", "fake@1", "consent-001", 0.97, keyring)
    path = tmp_path / "g.json"
    gallery_store.save(path, [e])
    doc = json.loads(path.read_text())
    assert doc["format"] == gallery_store.FORMAT
    assert set(doc["templates"][0]) == {"template_id", "employee_id", "model_id", "consent_ref", "quality", "ciphertext"}
    assert np.asarray(v, "<f4").tobytes() not in base64.b64decode(doc["templates"][0]["ciphertext"])
    [t] = gallery_store.decrypt_templates(gallery_store.load_entries(path), keyring)
    assert t.employee_id == "subj-01" and np.allclose(t.vector, v)


def test_gallery_rejects_relabelled_template(keyring: Keyring) -> None:
    e = gallery_store.make_entry(unit([1, 0, 0, 0]), "subj-01", "fake@1", "c1", 0.9, keyring)
    moved = dataclasses.replace(e, employee_id="subj-02")
    with pytest.raises(DecryptionError):
        gallery_store.decrypt_templates([moved], keyring)


def test_consent_reference_required(keyring: Keyring) -> None:
    with pytest.raises(ValueError, match="consent"):
        gallery_store.make_entry(unit([1, 0, 0, 0]), "subj-01", "fake@1", "  ", 0.9, keyring)
