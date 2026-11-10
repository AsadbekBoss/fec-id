"""Encrypted gallery file for EVALUATION use (until the Core API delivers templates, ARCHITECTURE §3.1).

* Every template is AES-256-GCM encrypted (same format and AAD binding as the Core API). The file
  never contains a plaintext vector.
* AAD = "gallery:{template_id}:{employee_id}:{model_id}": a ciphertext moved to another subject
  or model fails to decrypt.
* The file holds pseudonymous subject ids and consent references, never names or images.
* POSIX permissions are set to 0600. On Windows, keep the file in a folder only the service
  account can read.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from recognition.crypto import Keyring, decrypt_vector, encrypt_vector
from recognition.matcher import Template

FORMAT = "attendance-gallery/v1"


def _aad(template_id: str, employee_id: str, model_id: str) -> str:
    return f"gallery:{template_id}:{employee_id}:{model_id}"


@dataclass(frozen=True)
class StoredTemplate:
    template_id: str
    employee_id: str
    model_id: str
    consent_ref: str
    quality: float
    blob: bytes


def save(path: Path, entries: list[StoredTemplate]) -> None:
    doc = {
        "format": FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "templates": [
            {
                "template_id": e.template_id,
                "employee_id": e.employee_id,
                "model_id": e.model_id,
                "consent_ref": e.consent_ref,
                "quality": round(e.quality, 4),
                "ciphertext": base64.b64encode(e.blob).decode(),
            }
            for e in entries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover (Windows ACLs)
        pass


def load_entries(path: Path) -> list[StoredTemplate]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise ValueError("Unsupported gallery format")
    return [
        StoredTemplate(t["template_id"], t["employee_id"], t["model_id"], t["consent_ref"], t["quality"],
                       base64.b64decode(t["ciphertext"]))
        for t in doc["templates"]
    ]


def make_entry(vector: np.ndarray, employee_id: str, model_id: str, consent_ref: str, quality: float,
               keyring: Keyring) -> StoredTemplate:
    if not consent_ref.strip():
        raise ValueError("A consent reference is required for every template")
    tid = str(uuid.uuid4())
    return StoredTemplate(tid, employee_id, model_id, consent_ref, quality,
                          encrypt_vector(vector, _aad(tid, employee_id, model_id), keyring))


def decrypt_templates(entries: list[StoredTemplate], keyring: Keyring) -> list[Template]:
    return [
        Template(e.template_id, e.employee_id, e.model_id,
                 decrypt_vector(e.blob, _aad(e.template_id, e.employee_id, e.model_id), keyring))
        for e in entries
    ]
