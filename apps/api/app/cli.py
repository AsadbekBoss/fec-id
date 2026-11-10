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

from sqlalchemy import select

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cu = sub.add_parser("create-user", help="Create a user (password is prompted)")
    cu.add_argument("--username", required=True)
    cu.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    cu.add_argument("--email")
    args = parser.parse_args(argv)

    if args.command == "create-user":
        if not re.fullmatch(USERNAME_PATTERN, args.username):
            raise SystemExit("Username must be 3-64 chars: letters, digits, _ . @ -")
        password = _read_password(args.username)
        asyncio.run(_create_user(args.username, UserRole(args.role), args.email, password))


if __name__ == "__main__":
    main()
