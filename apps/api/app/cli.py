"""Administrative CLI.

    python -m app.cli create-user --username admin --role SUPER_ADMIN [--email a@b.c]

The password is read interactively (twice, not echoed). It is never accepted as an argument,
because arguments end up in shell history and process lists. Use this to create the first
SUPER_ADMIN; everything else goes through the API.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import re
import sys
from pathlib import Path

from sqlalchemy import select

from app import backup
from app.auth import passwords
from app.db import audit
from app.db.session import dispose_engine, session_scope
from app.models import User
from app.models.enums import ActorType, UserRole
from app.schemas.auth import USERNAME_PATTERN


def _read_password(username: str) -> str:
    for _ in range(3):
        pw = getpass.getpass("Password: ")
        if problems := passwords.policy_violations(pw, username):
            print("\n".join(f"  - {p}" for p in problems), file=sys.stderr)
            continue
        if getpass.getpass("Repeat password: ") != pw:
            print("  - Passwords do not match.", file=sys.stderr)
            continue
        return pw
    raise SystemExit("Aborted: no valid password entered.")


async def _create_user(username: str, role: UserRole, email: str | None, password: str) -> None:
    try:
        async with session_scope() as db:
            if (await db.execute(select(User.id).where(User.username == username))).first():
                raise SystemExit(f"User '{username}' already exists.")
            user = User(
                username=username,
                role=role,
                email=email,
                password_hash=await passwords.hash_password(password),
            )
            db.add(user)
            await db.flush()
            await audit.record(
                db,
                action="USER_CREATED",
                actor_type=ActorType.SERVICE,
                actor_service="cli",
                entity_type="user",
                entity_id=user.id,
                changes={"username": [None, username], "role": [None, role.value]},
            )
        print(f"Created {role.value} user '{username}'.")
    finally:
        await dispose_engine()


def _newest(folder: Path) -> Path:
    files = sorted(p for p in folder.glob("attendance-*.attbak"))
    if not files:
        raise backup.BackupError(f"No backups in {folder}")
    return files[-1]


def _backup_command(args: argparse.Namespace) -> None:
    if args.command == "backup":
        res = backup.create_backup(Path(args.out) if args.out else backup.DEFAULT_DIR, args.keep)
        rows = sum(res.tables.values())
        print(f"Backup written: {res.path}")
        print(
            f"  {res.size / 1024:.0f} KB, {len(res.tables)} tables, {rows} rows, "
            f"{res.storage_files} stored files"
        )
        print(
            "  Encrypted. It can only be restored with the encryption keys (npm run backup:keys)."
        )
    elif args.command == "backup-verify":
        path = Path(args.file) if args.file else _newest(backup.DEFAULT_DIR)
        info = backup.verify_backup(path)
        h = info["header"]
        print(f"OK: {path.name}")
        print(
            f"  created {h['created_at']}, schema {h['schema_version']}, "
            f"{sum(info['tables'].values())} rows, {info['storage_files']} stored files"
        )
    elif args.command == "backup-restore":
        info = backup.restore_backup(
            Path(args.file),
            args.database,
            storage_dir=Path(args.storage) if args.storage else None,
            force=args.force,
        )
        print(f"Restored into database '{info['database']}' ({info['storage_files']} files).")
    elif args.show:
        keys = backup.read_keys(Path(args.show), backup.ask_passphrase(confirm=False))
        for k in backup.KEY_FIELDS:
            print(f"{k}={keys[k]}")
        print(f"# exported_at {keys.get('exported_at')}")
    else:
        if not args.out:
            raise backup.BackupError(
                "Choose where to save the keys, AWAY from the backups (e.g. a USB stick): "
                "npm run backup:keys -- --out E:/attendance-keys.attkeys"
            )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        backup.export_keys(out, backup.ask_passphrase(confirm=True))
        print(f"Keys exported: {out}")
        print(
            "  Copy this file to a USB stick / password manager and keep it AWAY from the backups."
        )
        print(
            "  Without the passphrase it cannot be opened. Without the keys, backups are useless."
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cu = sub.add_parser("create-user", help="Create a user (password is prompted)")
    cu.add_argument("--username", required=True)
    cu.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    cu.add_argument("--email")
    bk = sub.add_parser("backup", help="Encrypted backup of the database + file storage")
    bk.add_argument("--out", default=None, help="Folder (default: <repo>/backups)")
    bk.add_argument("--keep", type=int, default=14, help="Keep the newest N backups (0 = all)")
    vf = sub.add_parser("backup-verify", help="Decrypt and check a backup (no changes made)")
    vf.add_argument("file", nargs="?", help="Backup file (default: newest)")
    rs = sub.add_parser("backup-restore", help="Restore a backup into an EXISTING, empty database")
    rs.add_argument("file")
    rs.add_argument("--database", required=True)
    rs.add_argument("--storage", help="Folder to restore stored files into (optional)")
    rs.add_argument("--force", action="store_true", help="Allow overwriting the live DB/storage")
    kx = sub.add_parser("backup-keys", help="Export encryption keys to a passphrase-protected file")
    kx.add_argument("--out", help="Write the key file here")
    kx.add_argument("--show", help="Decrypt a key file and print the keys (for disaster recovery)")
    args = parser.parse_args(argv)

    if args.command in ("backup", "backup-verify", "backup-restore", "backup-keys"):
        try:
            _backup_command(args)
        except backup.BackupError as e:
            raise SystemExit(f"ERROR: {e}") from None
        return

    if args.command == "create-user":
        if not re.fullmatch(USERNAME_PATTERN, args.username):
            raise SystemExit("Username must be 3-64 chars: letters, digits, _ . @ -")
        password = _read_password(args.username)
        asyncio.run(_create_user(args.username, UserRole(args.role), args.email, password))


if __name__ == "__main__":
    main()
