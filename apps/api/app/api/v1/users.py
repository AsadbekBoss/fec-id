"""/api/v1/users: system account administration (API_CONTRACTS §2.2)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.errors import AppError, conflict, not_found
from app.auth import passwords, service
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.db import audit
from app.models import User
from app.models.enums import ActorType, UserRole
from app.schemas.auth import PasswordReset, UserAdminOut, UserCreate, UserUpdate
from app.schemas.common import Page

router = APIRouter(prefix="/users", tags=["users"])

ReadUsers = Annotated[AuthContext, Depends(require(Permission.USERS_READ))]
WriteUsers = Annotated[AuthContext, Depends(require(Permission.USERS_WRITE))]


def _policy_error(problems: list[str], field: str = "password") -> AppError:
    return AppError(
        422,
        "VALIDATION_ERROR",
        "Validation failed",
        "Password does not meet the policy.",
        errors=[{"field": field, "code": "password_policy", "message": p} for p in problems],
    )


async def _get_user(db: DbSession, user_id: uuid.UUID, lock: bool = False) -> User:
    stmt = select(User).where(User.id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise not_found("User")
    return user


async def _other_active_super_admins(db: DbSession, exclude: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.SUPER_ADMIN, User.is_active.is_(True), User.id != exclude)
        )
    ).scalar_one()


@router.get("", response_model=Page[UserAdminOut], summary="List users")
async def list_users(
    _: ReadUsers,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    role: UserRole | None = None,
    is_active: bool | None = None,
    q: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[UserAdminOut]:
    conds: list[Any] = []
    if role is not None:
        conds.append(User.role == role)
    if is_active is not None:
        conds.append(User.is_active.is_(is_active))
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        conds.append(or_(User.username.ilike(like), User.email.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(User).where(*conds))).scalar_one()
    rows = (
        await db.execute(
            select(User)
            .where(*conds)
            .order_by(User.username)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return Page(
        items=[UserAdminOut.model_validate(u) for u in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "",
    response_model=UserAdminOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Username or email already exists"}, 422: {}},
    summary="Create a user",
)
async def create_user(
    body: UserCreate, ctx: WriteUsers, request: Request, db: DbSession
) -> UserAdminOut:
    if problems := passwords.policy_violations(body.password, body.username):
        raise _policy_error(problems)
    user = User(
        username=body.username,
        email=body.email,
        role=body.role,
        locale=body.locale,
        password_hash=await passwords.hash_password(body.password),
    )
    db.add(user)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise conflict(
            "USER_EXISTS", "A user with this username or email already exists."
        ) from None
    await audit.record(
        db,
        action="USER_CREATED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="user",
        entity_id=user.id,
        ip=client_ip(request),
        changes={"username": [None, user.username], "role": [None, user.role.value]},
    )
    await db.refresh(user)
    return UserAdminOut.model_validate(user)


@router.get("/{user_id}", response_model=UserAdminOut, responses={404: {}}, summary="Get a user")
async def get_user(user_id: uuid.UUID, _: ReadUsers, db: DbSession) -> UserAdminOut:
    return UserAdminOut.model_validate(await _get_user(db, user_id))


@router.patch(
    "/{user_id}",
    response_model=UserAdminOut,
    responses={404: {}, 409: {"description": "Would remove the last active SUPER_ADMIN"}},
    summary="Update role, status, email or locale",
)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, ctx: WriteUsers, request: Request, db: DbSession
) -> UserAdminOut:
    user = await _get_user(db, user_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise AppError(422, "VALIDATION_ERROR", "Validation failed", "No fields to update.")

    demoting = user.role == UserRole.SUPER_ADMIN and (
        changes.get("role", UserRole.SUPER_ADMIN) != UserRole.SUPER_ADMIN
        or changes.get("is_active") is False
    )
    if demoting and await _other_active_super_admins(db, user.id) == 0:
        raise conflict("LAST_SUPER_ADMIN", "At least one active SUPER_ADMIN must remain.")

    diff: dict[str, list[Any]] = {}
    for field, value in changes.items():
        old = getattr(user, field)
        if old != value:
            diff[field] = [
                old.value if hasattr(old, "value") else old,
                value.value if hasattr(value, "value") else value,
            ]
            setattr(user, field, value)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise conflict("USER_EXISTS", "A user with this email already exists.") from None

    if changes.get("is_active") is False:
        await service.revoke_user_sessions(db, user.id, "DEACTIVATED")
    if diff:
        await audit.record(
            db,
            action="USER_UPDATED",
            actor_type=ActorType.USER,
            actor_user_id=ctx.user_id,
            entity_type="user",
            entity_id=user.id,
            ip=client_ip(request),
            changes=diff,
        )
    await db.refresh(user)
    return UserAdminOut.model_validate(user)


@router.post(
    "/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}, 422: {}},
    summary="Set a new password for a user (signs out all their sessions)",
)
async def reset_password(
    user_id: uuid.UUID, body: PasswordReset, ctx: WriteUsers, request: Request, db: DbSession
) -> None:
    user = await _get_user(db, user_id, lock=True)
    if problems := passwords.policy_violations(body.new_password, user.username):
        raise _policy_error(problems, "new_password")
    user.password_hash = await passwords.hash_password(body.new_password)
    user.password_changed_at = func.now()
    user.failed_login_count = 0
    user.locked_until = None
    await service.revoke_user_sessions(db, user.id, "ADMIN")
    await audit.record(
        db,
        action="USER_PASSWORD_RESET",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="user",
        entity_id=user.id,
        ip=client_ip(request),
    )


@router.post(
    "/{user_id}/unlock",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}},
    summary="Clear a temporary login lock",
)
async def unlock_user(user_id: uuid.UUID, ctx: WriteUsers, request: Request, db: DbSession) -> None:
    user = await _get_user(db, user_id, lock=True)
    user.failed_login_count = 0
    user.locked_until = None
    await audit.record(
        db,
        action="USER_UNLOCKED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="user",
        entity_id=user.id,
        ip=client_ip(request),
    )


@router.delete(
    "/{user_id}/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}},
    summary="Sign the user out everywhere",
)
async def revoke_sessions(
    user_id: uuid.UUID, ctx: WriteUsers, request: Request, db: DbSession
) -> None:
    await _get_user(db, user_id)
    await service.revoke_user_sessions(db, user_id, "ADMIN")
    await audit.record(
        db,
        action="USER_SESSIONS_REVOKED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="user",
        entity_id=user_id,
        ip=client_ip(request),
    )
