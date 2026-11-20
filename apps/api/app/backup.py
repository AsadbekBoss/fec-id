"""Encrypted backups of the database and the encrypted file storage.

File layout (`*.attbak`):
    b"ATTBAK1\\n" | header length (4 bytes, big endian) | header JSON | ciphertext
* The header holds only non-personal metadata (time, schema version, sizes) and is bound to the
  ciphertext as AES-GCM associated data, so it cannot be altered unnoticed.
* The ciphertext is a tar.gz with `database.dump` (pg_dump custom format), `storage/` (already
  encrypted files) and `manifest.json` (SHA-256 of every member, row counts).
* Encryption uses the existing keyring with its own HKDF purpose ("backup-archive"). A backup is
  therefore USELESS WITHOUT THE KEYS: export them with `backup-keys` and keep that file apart.

Restoring never overwrites the live database or storage unless explicitly forced.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app import __version__
from app.core import crypto
from app.core.config import Settings, get_settings

MAGIC = b"ATTBAK1\n"
KEYS_MAGIC = b"ATTKEYS1\n"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR = REPO_ROOT / "backups"
NAME_RE = re.compile(r"^attendance-\d{8}-\d{6}\.attbak$")


class BackupError(RuntimeError):
    pass


# --- PostgreSQL client tools -------------------------------------------------------------------


def pg_tool(name: str) -> str:
    """pg_dump / pg_restore: PG_BIN_DIR, then PATH, then the newest Windows installation."""
    exe = name + (".exe" if os.name == "nt" else "")
    if d := os.environ.get("PG_BIN_DIR"):
        p = Path(d) / exe
        if p.is_file():
            return str(p)
    if found := shutil.which(name):
        return found
    candidates = sorted(
        Path("C:/Program Files/PostgreSQL").glob(f"*/bin/{exe}"),
        key=lambda p: int(p.parent.parent.name) if p.parent.parent.name.isdigit() else 0,
    )
    if candidates:
        return str(candidates[-1])
    raise BackupError(f"{name} not found. Install PostgreSQL client tools or set PG_BIN_DIR.")


def _pg_env(s: Settings) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PGHOST=s.postgres_host,
        PGPORT=str(s.postgres_port),
        PGUSER=s.postgres_user,
        PGPASSWORD=s.postgres_password.get_secret_value(),  # env, never on the command line
    )
    return env


def _run(cmd: list[str], env: dict[str, str], what: str) -> None:
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)  # noqa: S603
    if r.returncode != 0:
        raise BackupError(f"{what} failed: {r.stderr.strip()[-500:]}")


# --- archive encryption -------------------------------------------------------------------------


def _seal(payload: bytes, header: dict[str, Any], keyring: crypto.Keyring | None) -> bytes:
    h = json.dumps(header, sort_keys=True).encode()
    blob = crypto.encrypt(payload, crypto.Purpose.BACKUP, _aad(h), keyring)
    return MAGIC + struct.pack(">I", len(h)) + h + blob


def _aad(header: bytes) -> str:
    return "backup:" + hashlib.sha256(header).hexdigest()


def open_backup(path: Path, keyring: crypto.Keyring | None = None) -> tuple[dict[str, Any], bytes]:
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise BackupError("Not an attendance backup file.")
    (n,) = struct.unpack(">I", data[len(MAGIC) : len(MAGIC) + 4])
    h = data[len(MAGIC) + 4 : len(MAGIC) + 4 + n]
    try:
        payload = crypto.decrypt(
            data[len(MAGIC) + 4 + n :], crypto.Purpose.BACKUP, _aad(h), keyring
        )
    except crypto.DecryptionError:
        raise BackupError(
            "Backup cannot be decrypted: wrong encryption keys, or the file was modified."
        ) from None
    return json.loads(h), payload


# --- create -------------------------------------------------------------------------------------


@dataclass
class BackupResult:
    path: Path
    size: int
    tables: dict[str, int]
    storage_files: int


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_backup(
    out_dir: Path = DEFAULT_DIR, keep: int = 14, s: Settings | None = None
) -> BackupResult:
    s = s or get_settings()
    env = _pg_env(s)
    with tempfile.TemporaryDirectory(prefix="attbak-") as tmp:
        dump = Path(tmp) / "database.dump"
        _run(
            [
                pg_tool("pg_dump"),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--file={dump}",
                s.postgres_db,
            ],
            env,
            "pg_dump",
        )
        dump_bytes = dump.read_bytes()
        _run([pg_tool("pg_restore"), "--list", str(dump)], env, "pg_restore --list")  # readable?

    tables, version = _counts(s)
    storage = Path(s.storage_dir)
    if not storage.is_absolute():
        storage = Path.cwd() / storage
    files = [p for p in storage.rglob("*") if p.is_file()] if storage.is_dir() else []

    buf = io.BytesIO()
    manifest: dict[str, Any] = {"database.dump": _sha(dump_bytes), "storage": {}}
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add(tar, "database.dump", dump_bytes)
        for p in files:
            rel = p.relative_to(storage).as_posix()
            b = p.read_bytes()
            manifest["storage"][rel] = _sha(b)
            _add(tar, f"storage/{rel}", b)
        manifest.update(tables=tables, schema_version=version)
        _add(tar, "manifest.json", json.dumps(manifest, indent=1).encode())

    now = datetime.now(UTC)
    header = {
        "format": 1,
        "created_at": now.isoformat(),
        "app_version": __version__,
        "schema_version": version,
        "database": s.postgres_db,
        "dump_bytes": len(dump_bytes),
        "storage_files": len(files),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"attendance-{now:%Y%m%d-%H%M%S}.attbak"
    tmp_path = path.with_suffix(".partial")
    tmp_path.write_bytes(_seal(buf.getvalue(), header, None))
    os.replace(tmp_path, path)
    _prune(out_dir, keep)
    return BackupResult(path, path.stat().st_size, tables, len(files))


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(info, io.BytesIO(data))


def _prune(out_dir: Path, keep: int) -> None:
    backups = sorted(p for p in out_dir.iterdir() if NAME_RE.match(p.name))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()


def _counts(s: Settings) -> tuple[dict[str, int], str | None]:
    """Row counts per table and the Alembic version, via psql-free asyncpg."""
    import asyncio

    import asyncpg  # type: ignore[import-untyped]

    async def go() -> tuple[dict[str, int], str | None]:
        conn = await asyncpg.connect(
            host=s.postgres_host,
            port=s.postgres_port,
            user=s.postgres_user,
            password=s.postgres_password.get_secret_value(),
            database=s.postgres_db,
        )
        try:
            names = [
                r[0]
                for r in await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
                )
            ]
            counts = {n: int(await conn.fetchval(f'SELECT count(*) FROM "{n}"')) for n in names}  # noqa: S608 (names from pg_tables)
            version = (
                await conn.fetchval("SELECT version_num FROM alembic_version")
                if ("alembic_version" in names)
                else None
            )
            return counts, version
        finally:
            await conn.close()

    return asyncio.run(go())


# --- verify / restore ---------------------------------------------------------------------------


def _unpack(payload: bytes, dest: Path) -> dict[str, Any]:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")  # safe extraction (no absolute paths / links)
    manifest: dict[str, Any] = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    if _sha((dest / "database.dump").read_bytes()) != manifest["database.dump"]:
        raise BackupError("Database dump checksum mismatch.")
    for rel, digest in manifest["storage"].items():
        if _sha((dest / "storage" / rel).read_bytes()) != digest:
            raise BackupError(f"Storage file checksum mismatch: {rel}")
    return manifest


def verify_backup(path: Path, s: Settings | None = None) -> dict[str, Any]:
    s = s or get_settings()
    header, payload = open_backup(path)
    with tempfile.TemporaryDirectory(prefix="attbak-") as tmp:
        manifest = _unpack(payload, Path(tmp))
        _run(
            [pg_tool("pg_restore"), "--list", str(Path(tmp) / "database.dump")],
            _pg_env(s),
            "pg_restore --list",
        )
    return {
        "header": header,
        "tables": manifest["tables"],
        "storage_files": len(manifest["storage"]),
    }


def restore_backup(
    path: Path, database: str, *, storage_dir: Path | None, force: bool, s: Settings | None = None
) -> dict[str, Any]:
    """Restore into `database` (must exist; use an EMPTY one). The live database and storage are
    only overwritten with force=True."""
    s = s or get_settings()
    if database == s.postgres_db and not force:
        raise BackupError(f"Refusing to overwrite the live database '{database}' without --force.")
    header, payload = open_backup(path)
    with tempfile.TemporaryDirectory(prefix="attbak-") as tmp:
        manifest = _unpack(payload, Path(tmp))
        _run(
            [
                pg_tool("pg_restore"),
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                f"--dbname={database}",
                str(Path(tmp) / "database.dump"),
            ],
            _pg_env(s),
            "pg_restore",
        )
        restored_files = 0
        if storage_dir is not None:
            if storage_dir.exists() and any(storage_dir.iterdir()) and not force:
                raise BackupError(f"Storage folder {storage_dir} is not empty (use --force).")
            src = Path(tmp) / "storage"
            if src.is_dir():
                shutil.copytree(src, storage_dir, dirs_exist_ok=True)
                restored_files = len(manifest["storage"])
    return {"header": header, "database": database, "storage_files": restored_files}


# --- key export (passphrase protected) ----------------------------------------------------------

KEY_FIELDS = (
    "DATA_ENCRYPTION_KEYS",
    "DATA_ENCRYPTION_ACTIVE_KEY_ID",
    "JWT_SIGNING_KEYS",
    "JWT_ACTIVE_KEY_ID",
)


def _kdf(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def export_keys(out: Path, passphrase: str, s: Settings | None = None) -> None:
    s = s or get_settings()
    if len(passphrase) < 12:
        raise BackupError("Passphrase must be at least 12 characters.")
    secrets = {
        "DATA_ENCRYPTION_KEYS": s.data_encryption_keys.get_secret_value(),
        "DATA_ENCRYPTION_ACTIVE_KEY_ID": str(s.data_encryption_active_key_id),
        "JWT_SIGNING_KEYS": s.jwt_signing_keys.get_secret_value(),
        "JWT_ACTIVE_KEY_ID": str(s.jwt_active_key_id),
        "exported_at": datetime.now(UTC).isoformat(),
    }
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_kdf(passphrase, salt)).encrypt(nonce, json.dumps(secrets).encode(), KEYS_MAGIC)
    out.write_bytes(KEYS_MAGIC + base64.b64encode(salt + nonce + ct) + b"\n")


def read_keys(path: Path, passphrase: str) -> dict[str, str]:
    data = path.read_bytes()
    if not data.startswith(KEYS_MAGIC):
        raise BackupError("Not a key export file.")
    raw = base64.b64decode(data[len(KEYS_MAGIC) :].strip())
    salt, nonce, ct = raw[:16], raw[16:28], raw[28:]
    try:
        return dict(json.loads(AESGCM(_kdf(passphrase, salt)).decrypt(nonce, ct, KEYS_MAGIC)))
    except InvalidTag:
        raise BackupError("Wrong passphrase or damaged key file.") from None


def ask_passphrase(confirm: bool) -> str:
    p = getpass.getpass("Passphrase (min 12 chars): ")
    if confirm and getpass.getpass("Repeat passphrase: ") != p:
        raise BackupError("Passphrases do not match.")
    return p
