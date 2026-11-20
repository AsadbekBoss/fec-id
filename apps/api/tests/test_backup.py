"""Encrypted backups: format, tamper/wrong-key detection, key export, and a real
pg_dump -> verify -> pg_restore round trip on the test database."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.engine import URL

from app import backup
from app.core import crypto
from app.core.config import Settings, get_settings
from tests.conftest import create_database, drop_database, requires_db

KEY2 = "2:" + "A" * 43


def test_seal_open_roundtrip_and_tamper(tmp_path: Path) -> None:
    f = tmp_path / "x.attbak"
    f.write_bytes(backup._seal(b"payload", {"format": 1, "created_at": "t"}, None))
    header, data = backup.open_backup(f)
    assert header["format"] == 1 and data == b"payload"
    raw = bytearray(f.read_bytes())
    raw[-1] ^= 1
    f.write_bytes(bytes(raw))
    with pytest.raises(backup.BackupError, match="cannot be decrypted"):
        backup.open_backup(f)
    # changing the (plaintext) header is detected too: it is bound as associated data
    g = tmp_path / "y.attbak"
    g.write_bytes(
        backup._seal(b"p", {"format": 1, "created_at": "a"}, None).replace(b'"a"', b'"b"')
    )
    with pytest.raises(backup.BackupError):
        backup.open_backup(g)


def test_wrong_keys_cannot_open(tmp_path: Path) -> None:
    f = tmp_path / "x.attbak"
    f.write_bytes(backup._seal(b"secret", {"format": 1}, None))
    other = crypto.Keyring.parse(KEY2, 2)
    with pytest.raises(backup.BackupError):
        backup.open_backup(f, other)
    (tmp_path / "junk.attbak").write_bytes(b"hello")
    with pytest.raises(backup.BackupError, match="Not an attendance backup"):
        backup.open_backup(tmp_path / "junk.attbak")


def test_key_export_requires_passphrase(tmp_path: Path) -> None:
    out = tmp_path / "k.attkeys"
    backup.export_keys(out, "correct horse battery")
    s = get_settings()
    assert s.data_encryption_keys.get_secret_value() not in out.read_text()
    keys = backup.read_keys(out, "correct horse battery")
    assert keys["DATA_ENCRYPTION_KEYS"] == s.data_encryption_keys.get_secret_value()
    with pytest.raises(backup.BackupError, match="Wrong passphrase"):
        backup.read_keys(out, "wrong passphrase!!")
    with pytest.raises(backup.BackupError, match="12"):
        backup.export_keys(out, "short")


def test_prune_keeps_newest(tmp_path: Path) -> None:
    for d in ("20261101", "20261102", "20261103"):
        (tmp_path / f"attendance-{d}-120000.attbak").write_bytes(b"x")
    (tmp_path / "other.txt").write_text("keep")
    backup._prune(tmp_path, 2)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "attendance-20261102-120000.attbak",
        "attendance-20261103-120000.attbak",
        "other.txt",
    ]


def _settings_for(url: URL, storage: Path) -> Settings:
    return get_settings().model_copy(
        update={
            "postgres_host": url.host or "127.0.0.1",
            "postgres_port": url.port or 5432,
            "postgres_user": url.username,
            "postgres_db": url.database,
            "postgres_password": type(get_settings().postgres_password)(url.password or ""),
            "storage_dir": str(storage),
        }
    )


@requires_db
def test_full_backup_verify_restore(migrated_db_url: URL, tmp_path: Path) -> None:
    try:
        backup.pg_tool("pg_dump")
    except backup.BackupError:
        pytest.skip("pg_dump not installed")
    storage = tmp_path / "storage"
    (storage / "photos").mkdir(parents=True)
    (storage / "photos" / "a.bin").write_bytes(os.urandom(64))
    s = _settings_for(migrated_db_url, storage)  # URL object: str() would mask the password

    res = backup.create_backup(tmp_path / "out", keep=5, s=s)
    assert res.path.exists() and res.storage_files == 1 and "employees" in res.tables
    assert b"employees" not in res.path.read_bytes()  # nothing readable inside
    info = backup.verify_backup(res.path, s=s)
    assert info["storage_files"] == 1 and info["header"]["schema_version"]

    target = create_database()
    try:
        with pytest.raises(backup.BackupError, match="live database"):
            backup.restore_backup(res.path, s.postgres_db, storage_dir=None, force=False, s=s)
        restored_storage = tmp_path / "restored"
        out = backup.restore_backup(
            res.path, str(target.database), storage_dir=restored_storage, force=False, s=s
        )
        assert out["storage_files"] == 1
        assert (restored_storage / "photos" / "a.bin").read_bytes() == (
            storage / "photos" / "a.bin"
        ).read_bytes()
        counts, version = backup._counts(_settings_for(target, storage))
        assert version == info["header"]["schema_version"] and counts.keys() == res.tables.keys()
    finally:
        drop_database(target)
