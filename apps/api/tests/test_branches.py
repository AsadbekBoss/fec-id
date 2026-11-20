"""Branches (Phase 21): a branch user sees and changes only its own branch; the super
administrator sees every branch or the one chosen with X-Branch-Id."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import service as att
from app.models.enums import AttendanceMethod, CameraDirection, RecognitionDecision, UserRole
from app.models.organization import MAIN_BRANCH_ID
from tests import factories as f
from tests.api_helpers import bearer, login, make_user, oid, token_for
from tests.conftest import requires_db

pytestmark = requires_db


async def _two_branches(session: AsyncSession) -> dict[str, Any]:
    """Branch A and B, each with a department, an employee and an ADMIN user."""
    out: dict[str, Any] = {}
    for key in ("a", "b"):
        b = await f.branch(session)
        d = await f.department(session, branch_id=b.id)
        ws = await f.schedule(session, branch_id=b.id, working_days=[1, 2, 3, 4, 5, 6, 7])
        d.work_schedule_id = ws.id
        e = await f.employee(session, d, attendance_method=AttendanceMethod.FACE)
        admin = await make_user(session, role=UserRole.ADMIN, branch_id=b.id)
        out[key] = {"branch": b.id, "dept": d.id, "ws": ws.id, "emp": e.id, "admin": admin}
    await session.flush()
    return out


async def _auth(api: httpx.AsyncClient, acc: Any, branch: Any = None) -> dict[str, str]:
    h = bearer(await token_for(api, acc))
    if branch is not None:
        h["X-Branch-Id"] = str(branch)
    return h


async def test_branch_admin_sees_only_own_employees(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    o = await _two_branches(session)
    h = await _auth(api, o["a"]["admin"])
    ids = {e["id"] for e in (await api.get("/api/v1/employees", headers=h)).json()["items"]}
    assert str(o["a"]["emp"]) in ids and str(o["b"]["emp"]) not in ids
    # another branch's rows do not exist for this user: 404, never 403
    assert (await api.get(f"/api/v1/employees/{o['b']['emp']}", headers=h)).status_code == 404
    r = await api.patch(f"/api/v1/employees/{o['b']['emp']}", headers=h, json={"first_name": "X"})
    assert r.status_code == 404
    depts = {d["id"] for d in (await api.get("/api/v1/departments", headers=h)).json()}
    assert depts == {str(o["a"]["dept"])}
    schedules = {w["id"] for w in (await api.get("/api/v1/work-schedules", headers=h)).json()}
    assert schedules == {str(o["a"]["ws"])}
    r = await api.get(f"/api/v1/attendance/days/{o['b']['emp']}/2026-11-19", headers=h)
    assert r.status_code == 404


async def test_branch_admin_cannot_use_other_branch_references(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    o = await _two_branches(session)
    h = await _auth(api, o["a"]["admin"])
    body = {
        "employee_code": "X1",
        "last_name": "Aliyev",
        "first_name": "Ali",
        "hire_date": "2026-01-10",
    }
    r = await api.post(
        "/api/v1/employees", headers=h, json={**body, "department_id": str(o["b"]["dept"])}
    )
    assert r.status_code == 422
    r = await api.post(
        "/api/v1/employees",
        headers=h,
        json={**body, "department_id": str(o["a"]["dept"]), "work_schedule_id": str(o["b"]["ws"])},
    )
    assert r.status_code == 422
    r = await api.post(
        "/api/v1/employees", headers=h, json={**body, "department_id": str(o["a"]["dept"])}
    )
    assert r.status_code == 201, r.text
    assert r.json()["branch_id"] == str(o["a"]["branch"])
    # a new department lands in the user's own branch
    r = await api.post("/api/v1/departments", headers=h, json={"name": "Yangi bo'lim"})
    assert r.status_code == 201 and r.json()["branch_id"] == str(o["a"]["branch"])


async def test_codes_are_unique_per_branch(api: httpx.AsyncClient, session: AsyncSession) -> None:
    o = await _two_branches(session)
    body = {"last_name": "Aliyev", "first_name": "Ali", "hire_date": "2026-01-10"}
    for key in ("a", "b"):
        h = await _auth(api, o[key]["admin"])
        r = await api.post(
            "/api/v1/employees",
            headers=h,
            json={**body, "employee_code": "E100", "department_id": str(o[key]["dept"])},
        )
        assert r.status_code == 201, r.text
    h = await _auth(api, o["a"]["admin"])
    r = await api.post(
        "/api/v1/employees",
        headers=h,
        json={**body, "employee_code": "E100", "department_id": str(o["a"]["dept"])},
    )
    assert r.status_code == 409 and r.json()["code"] == "EMPLOYEE_CODE_EXISTS"


async def test_super_admin_sees_all_or_the_selected_branch(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    o = await _two_branches(session)
    boss = await make_user(session, role=UserRole.SUPER_ADMIN)
    h = await _auth(api, boss)
    ids = {e["id"] for e in (await api.get("/api/v1/employees", headers=h)).json()["items"]}
    assert {str(o["a"]["emp"]), str(o["b"]["emp"])} <= ids
    hb = await _auth(api, boss, o["b"]["branch"])
    ids = {e["id"] for e in (await api.get("/api/v1/employees", headers=hb)).json()["items"]}
    assert str(o["b"]["emp"]) in ids and str(o["a"]["emp"]) not in ids
    bad = {**h, "X-Branch-Id": str(uuid.uuid4())}
    assert (await api.get("/api/v1/employees", headers=bad)).status_code == 422
    # a branch user cannot widen its view with the header
    ha = await _auth(api, o["a"]["admin"], o["b"]["branch"])
    ids = {e["id"] for e in (await api.get("/api/v1/employees", headers=ha)).json()["items"]}
    assert str(o["b"]["emp"]) not in ids


async def test_branch_management_is_super_admin_only(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    o = await _two_branches(session)
    h = await _auth(api, o["a"]["admin"])
    assert (await api.post("/api/v1/branches", headers=h, json={"name": "Z"})).status_code == 403
    assert (await api.get("/api/v1/system/status", headers=h)).status_code == 403
    mine = (await api.get("/api/v1/branches", headers=h)).json()
    assert [b["id"] for b in mine] == [str(o["a"]["branch"])]
    me = (await api.get("/api/v1/auth/me", headers=h)).json()
    assert me["branch"]["id"] == str(o["a"]["branch"]) and me["all_branches"] is False

    boss = await make_user(session, role=UserRole.SUPER_ADMIN)
    hs = await _auth(api, boss)
    r = await api.post("/api/v1/branches", headers=hs, json={"name": "Chilonzor"})
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert (
        await api.post("/api/v1/branches", headers=hs, json={"name": "chilonzor"})
    ).status_code == 409
    assert (await api.delete(f"/api/v1/branches/{MAIN_BRANCH_ID}", headers=hs)).status_code == 409
    r = await api.patch(f"/api/v1/branches/{MAIN_BRANCH_ID}", headers=hs, json={"is_active": False})
    assert r.status_code == 409
    r = await api.delete(f"/api/v1/branches/{o['a']['branch']}", headers=hs)
    assert r.status_code == 409 and r.json()["code"] == "BRANCH_NOT_EMPTY"
    assert (await api.delete(f"/api/v1/branches/{new_id}", headers=hs)).status_code == 204


async def test_closing_a_branch_locks_out_its_users(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    o = await _two_branches(session)
    h = await _auth(api, o["a"]["admin"])
    boss = await make_user(session, role=UserRole.SUPER_ADMIN)
    hs = await _auth(api, boss)
    r = await api.patch(
        f"/api/v1/branches/{o['a']['branch']}", headers=hs, json={"is_active": False}
    )
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = await api.get("/api/v1/employees", headers=h)
    assert r.status_code == 401 and r.json()["code"] == "BRANCH_INACTIVE"
    assert (await login(api, o["a"]["admin"].username)).status_code == 401
    assert (await login(api, o["b"]["admin"].username)).status_code == 200


async def test_camera_counts_only_its_branch(session: AsyncSession) -> None:
    o = await _two_branches(session)
    cam = await f.camera(
        session,
        name=f"Kirish {uuid.uuid4().hex[:6]}",
        direction=CameraDirection.IN,
        recognition_enabled=True,
        branch_id=o["a"]["branch"],
    )

    async def seen(emp_id: Any) -> Any:
        r = await att.ingest_camera_event(
            session,
            event_uid=uuid.uuid4(),
            camera_id=cam.id,
            event_time=datetime.now(UTC) - timedelta(seconds=5),
            decision=RecognitionDecision.MATCHED,
            employee_id=emp_id,
            model_id="sface@2021dec",
            similarity=0.8,
        )
        return r.event

    assert (await seen(o["a"]["emp"])).counts_for_attendance
    other = await seen(o["b"]["emp"])
    assert other.counts_for_attendance is False and other.ignore_reason == "OTHER_BRANCH"


async def test_users_and_cameras_are_scoped(api: httpx.AsyncClient, session: AsyncSession) -> None:
    o = await _two_branches(session)
    cam_b = await f.camera(session, name=f"B {uuid.uuid4().hex[:6]}", branch_id=o["b"]["branch"])
    cam_b_id = oid(cam_b)
    h = await _auth(api, o["a"]["admin"])
    users = {u["id"] for u in (await api.get("/api/v1/users", headers=h)).json()["items"]}
    assert str(o["a"]["admin"].id) in users and str(o["b"]["admin"].id) not in users
    assert (await api.get(f"/api/v1/users/{o['b']['admin'].id}", headers=h)).status_code == 404
    cams = {c["id"] for c in (await api.get("/api/v1/cameras", headers=h)).json()["items"]}
    assert str(cam_b_id) not in cams
    assert (await api.get(f"/api/v1/cameras/{cam_b_id}", headers=h)).status_code == 404

    boss = await make_user(session, role=UserRole.SUPER_ADMIN)
    hs = await _auth(api, boss)
    r = await api.post(
        "/api/v1/users",
        headers=hs,
        json={
            "username": f"b_admin_{uuid.uuid4().hex[:6]}",
            "password": "Yaxshi-parol-2026!",
            "role": "ADMIN",
            "branch_id": str(o["b"]["branch"]),
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["branch_id"] == str(o["b"]["branch"])
    # camera departments must belong to the camera's branch
    r = await api.patch(
        f"/api/v1/cameras/{cam_b_id}",
        headers=hs,
        json={"department_ids": [str(o["a"]["dept"])]},
    )
    assert r.status_code == 422


async def test_overview_counts_every_branch(api: httpx.AsyncClient, session: AsyncSession) -> None:
    o = await _two_branches(session)
    boss = await make_user(session, role=UserRole.SUPER_ADMIN)
    hs = await _auth(api, boss)
    r = await api.get("/api/v1/branches/overview", headers=hs, params={"date": "2026-11-19"})
    assert r.status_code == 200, r.text
    by_id = {b["branch"]["id"]: b for b in r.json()["branches"]}
    for key in ("a", "b"):
        assert by_id[str(o[key]["branch"])]["totals"]["EMPLOYEES"] == 1
        assert by_id[str(o[key]["branch"])]["branch"]["employee_count"] == 1
    h = await _auth(api, o["a"]["admin"])
    r = await api.get("/api/v1/branches/overview", headers=h, params={"date": "2026-11-19"})
    assert r.status_code == 403


async def test_cors_allows_the_branch_header(api: httpx.AsyncClient) -> None:
    """The browser sends X-Branch-Id from another origin: the preflight must allow it."""
    from app.core.config import get_settings

    origin = get_settings().cors_origins[0]
    r = await api.options(
        "/api/v1/employees",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-branch-id",
        },
    )
    assert r.status_code == 200, r.text
    assert "x-branch-id" in r.headers["access-control-allow-headers"].lower()
