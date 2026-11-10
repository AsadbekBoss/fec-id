"""Employee management API: CRUD, search, status, photo, consent, departments, RBAC."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AuditLog, Employee, FaceEmbedding
from app.models.enums import UserRole
from tests import factories as f
from tests.api_helpers import audit_actions, bearer, make_user, token_for
from tests.conftest import requires_db

pytestmark = requires_db

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))
    yield tmp_path


@pytest.fixture
async def tokens(api: httpx.AsyncClient, session: AsyncSession) -> dict[str, dict[str, str]]:
    out = {}
    for role in UserRole:
        u = await make_user(session, role=role)
        out[role.value] = bearer(await token_for(api, u))
    return out


@pytest.fixture
async def dept(session: AsyncSession) -> uuid.UUID:
    return (await f.department(session, name="Bosh ofis")).id


def payload(dept_id: uuid.UUID, **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "employee_code": f"e-{uuid.uuid4().hex[:6]}",
        "last_name": "Karimov",
        "first_name": "Aziz",
        "department_id": str(dept_id),
        "hire_date": "2024-03-01",
    }
    base.update(kw)
    return base


async def create(
    api: httpx.AsyncClient, h: dict[str, str], dept_id: uuid.UUID, **kw: object
) -> dict:  # type: ignore[type-arg]
    r = await api.post("/api/v1/employees", json=payload(dept_id, **kw), headers=h)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def jpeg(size: tuple[int, int] = (300, 300)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, format="JPEG")
    return out.getvalue()


# --- create / validate / duplicates -------------------------------------------------------


async def test_create_normalises_and_audits(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    e = await create(
        api,
        tokens["ADMIN"],
        dept,
        employee_code=" ab-001 ",
        last_name="  Qo’chqorov ",
        middle_name="Baxtiyor o'g'li",
        phone="+998 (90) 123-45-67",
        email="Aziz@Mail.UZ",
    )
    assert e["employee_code"] == "AB-001"
    assert e["last_name"] == "Qoʻchqorov" and e["middle_name"] == "Baxtiyor oʻgʻli"
    assert e["full_name"] == "Qoʻchqorov Aziz Baxtiyor oʻgʻli"
    assert e["phone"] == "+998901234567" and e["email"] == "aziz@mail.uz"
    assert e["status"] == "ACTIVE" and e["is_active"] is True
    assert e["attendance_method"] == "FACE" and e["has_photo"] is False
    assert e["biometric"] == {
        "status": "NO_CONSENT",
        "consent_id": None,
        "consent_granted_at": None,
        "consent_documented": False,
        "face_templates": 0,
        "can_enroll": False,
    }
    assert "EMPLOYEE_CREATED" in await audit_actions(session)


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"employee_code": "bad code!"}, "employee_code"),
        ({"employee_code": ""}, "employee_code"),
        ({"last_name": "Karimov2"}, "last_name"),
        ({"first_name": "   "}, "first_name"),
        ({"phone": "12-34"}, "phone"),
        ({"email": "not-an-email"}, "email"),
        ({"hire_date": "1900-01-01"}, "hire_date"),
        ({"hire_date": (date.today() + timedelta(days=800)).isoformat()}, "hire_date"),
        ({"department_id": "not-a-uuid"}, "department_id"),
        ({"attendance_method": "FINGERPRINT"}, "attendance_method"),
        ({"status": "ACTIVE"}, "status"),  # unknown field
    ],
)
async def test_create_validation(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
    override: dict[str, object],
    field: str,
) -> None:
    r = await api.post("/api/v1/employees", json=payload(dept, **override), headers=tokens["ADMIN"])
    assert r.status_code == 422, r.text
    assert any(field in err["field"] for err in r.json()["errors"]), r.json()


async def test_create_unknown_department(api: httpx.AsyncClient, tokens: dict) -> None:  # type: ignore[type-arg]
    r = await api.post("/api/v1/employees", json=payload(uuid.uuid4()), headers=tokens["ADMIN"])
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "department_id"


async def test_duplicates_prevented(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    await create(api, h, dept, employee_code="E100", phone="+998901112233", email="a@b.uz")
    cases = [
        ({"employee_code": "e100"}, "EMPLOYEE_CODE_EXISTS"),
        ({"email": "A@B.UZ"}, "EMPLOYEE_EMAIL_EXISTS"),
        ({"phone": "+998 90 111 22 33"}, "EMPLOYEE_PHONE_EXISTS"),
    ]
    for override, code in cases:
        r = await api.post("/api/v1/employees", json=payload(dept, **override), headers=h)
        assert r.status_code == 409 and r.json()["code"] == code, r.text


# --- read / list / search / filter / pagination -------------------------------------------


async def test_get_detail_and_not_found(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    e = await create(api, tokens["ADMIN"], dept)
    r = await api.get(f"/api/v1/employees/{e['id']}", headers=tokens["VIEWER"])
    assert r.status_code == 200 and r.json()["id"] == e["id"]
    assert (
        await api.get(f"/api/v1/employees/{uuid.uuid4()}", headers=tokens["VIEWER"])
    ).status_code == 404
    assert (await api.get("/api/v1/employees/123", headers=tokens["VIEWER"])).status_code == 422


async def test_list_pagination_search_filters(
    api: httpx.AsyncClient,
    tokens: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    root = await f.department(session, name="HQ")
    sub = await f.department(session, name="Buxgalteriya", parent_id=root.id)
    other = await f.department(session, name="Ombor")
    await create(api, h, root.id, employee_code="A1", last_name="Qoʻchqorov", first_name="Aziz")
    await create(api, h, sub.id, employee_code="A2", last_name="Ivanov", first_name="Sergey")
    await create(
        api,
        h,
        sub.id,
        employee_code="A3",
        last_name="Petrova",
        first_name="Anna",
        attendance_method="MANUAL",
    )
    await create(api, h, other.id, employee_code="B1", last_name="Rahimova", first_name="Dilnoza")
    await create(api, h, other.id, employee_code="B2", last_name="Toshmatov", first_name="Bek")

    v = tokens["VIEWER"]

    async def ids(query: str) -> list[str]:
        r = await api.get(f"/api/v1/employees?{query}", headers=v)
        assert r.status_code == 200, r.text
        return [i["employee_code"] for i in r.json()["items"]]

    r = await api.get("/api/v1/employees?page_size=2&page=3&sort=employee_code", headers=v)
    body = r.json()
    assert (
        body["total"] == 5
        and body["page"] == 3
        and [i["employee_code"] for i in body["items"]] == ["B2"]
    )

    assert await ids("q=qo'chq") == ["A1"]  # apostrophe variants match
    assert await ids("q=QOʻCHQOROV") == ["A1"]  # case-insensitive
    assert await ids("q=b1") == ["B1"]  # code search
    assert await ids("q=%25") == []  # LIKE wildcards are escaped
    assert sorted(await ids(f"department_id={root.id}")) == ["A1", "A2", "A3"]
    assert await ids(f"department_id={root.id}&include_subdepartments=false") == ["A1"]
    assert await ids("attendance_method=MANUAL") == ["A3"]
    assert await ids("biometric_status=NOT_APPLICABLE") == ["A3"]
    assert await ids("sort=-employee_code&page_size=2") == ["B2", "B1"]

    for bad in ("page_size=500", "page=0", "sort=password_hash", "q=" + "x" * 101, "status=GONE"):
        assert (await api.get(f"/api/v1/employees?{bad}", headers=v)).status_code == 422, bad


# --- update -------------------------------------------------------------------------------


async def test_update_partial_and_audit_diff(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    e = await create(api, h, dept, position_title="Hisobchi")
    r = await api.patch(
        f"/api/v1/employees/{e['id']}",
        headers=h,
        json={"position_title": "Bosh hisobchi", "phone": "+998 91 000 00 01"},
    )
    assert r.status_code == 200
    assert r.json()["position_title"] == "Bosh hisobchi" and r.json()["last_name"] == "Karimov"
    entry = (
        await session.execute(select(AuditLog).where(AuditLog.action == "EMPLOYEE_UPDATED"))
    ).scalar_one()
    assert entry.changes == {
        "position_title": ["Hisobchi", "Bosh hisobchi"],
        "phone": [None, "+998910000001"],
    }

    for bad in ({}, {"last_name": None}, {"hire_date": None}, {"employee_code": "x y"}):
        assert (
            await api.patch(f"/api/v1/employees/{e['id']}", headers=h, json=bad)
        ).status_code == 422

    clear = await api.patch(
        f"/api/v1/employees/{e['id']}", headers=h, json={"position_title": None}
    )
    assert clear.status_code == 200 and clear.json()["position_title"] is None


async def test_update_duplicate_rejected(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    await create(api, h, dept, employee_code="X1")
    e2 = await create(api, h, dept, employee_code="X2")
    r = await api.patch(f"/api/v1/employees/{e2['id']}", headers=h, json={"employee_code": "x1"})
    assert r.status_code == 409 and r.json()["code"] == "EMPLOYEE_CODE_EXISTS"
    same = await api.patch(f"/api/v1/employees/{e2['id']}", headers=h, json={"employee_code": "X2"})
    assert same.status_code == 200  # unchanged own value is not a duplicate


# --- activate / deactivate ----------------------------------------------------------------


async def test_status_transitions(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    e = await create(api, h, dept, hire_date="2024-03-01")
    url = f"/api/v1/employees/{e['id']}"

    r = await api.post(
        f"{url}/deactivate", headers=h, json={"status": "SUSPENDED", "reason": "Tergov"}
    )
    assert (
        r.status_code == 200
        and r.json()["status"] == "SUSPENDED"
        and r.json()["is_active"] is False
    )
    again = await api.post(
        f"{url}/deactivate", headers=h, json={"status": "SUSPENDED", "reason": "x y z"}
    )
    assert again.status_code == 409 and again.json()["code"] == "INVALID_STATUS_TRANSITION"

    bad = await api.post(
        f"{url}/deactivate",
        headers=h,
        json={"status": "TERMINATED", "termination_date": "2020-01-01", "reason": "Ketdi"},
    )
    assert bad.status_code == 422
    no_reason = await api.post(f"{url}/deactivate", headers=h, json={"status": "TERMINATED"})
    assert no_reason.status_code == 422
    mixed = await api.post(
        f"{url}/deactivate",
        headers=h,
        json={"status": "SUSPENDED", "termination_date": "2025-01-01", "reason": "abc"},
    )
    assert mixed.status_code == 422

    t = await api.post(
        f"{url}/deactivate", headers=h, json={"status": "TERMINATED", "reason": "Ishdan ketdi"}
    )
    assert t.status_code == 200 and t.json()["status"] == "TERMINATED"
    assert t.json()["termination_date"] == datetime.now(UTC).date().isoformat()

    a = await api.post(f"{url}/activate", headers=h, json={"reason": "Qayta ishga olindi"})
    assert (
        a.status_code == 200
        and a.json()["status"] == "ACTIVE"
        and a.json()["termination_date"] is None
    )
    assert (await api.post(f"{url}/activate", headers=h)).status_code == 409


# --- RBAC ---------------------------------------------------------------------------------


async def test_viewer_is_read_only(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    e = await create(api, tokens["ADMIN"], dept)
    v = tokens["VIEWER"]
    url = f"/api/v1/employees/{e['id']}"
    assert (await api.get("/api/v1/employees", headers=v)).status_code == 200
    assert (await api.get(url, headers=v)).status_code == 200
    forbidden = [
        api.post("/api/v1/employees", json=payload(dept), headers=v),
        api.patch(url, json={"position_title": "x"}, headers=v),
        api.post(f"{url}/deactivate", json={"status": "SUSPENDED", "reason": "abc"}, headers=v),
        api.post(f"{url}/activate", headers=v),
        api.put(f"{url}/photo", files={"file": ("p.jpg", jpeg(), "image/jpeg")}, headers=v),
        api.delete(f"{url}/photo", headers=v),
        api.get(f"{url}/consents", headers=v),
        api.post(
            f"{url}/consents",
            json={"consent_text_version": "2026-11-v1", "employee_signed": True},
            headers=v,
        ),
        api.get("/api/v1/biometrics/consent-version", headers=v),
        api.post("/api/v1/departments", json={"name": "New"}, headers=v),
    ]
    for coro in forbidden:
        r = await coro
        assert r.status_code == 403, (r.request.method, r.request.url, r.text)


async def test_super_admin_and_admin_can_modify(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    for role in ("SUPER_ADMIN", "ADMIN"):
        e = await create(api, tokens[role], dept)
        r = await api.patch(
            f"/api/v1/employees/{e['id']}", headers=tokens[role], json={"position_title": role}
        )
        assert r.status_code == 200


# --- photo --------------------------------------------------------------------------------


async def test_photo_lifecycle(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,
    storage_dir: Path,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    e = await create(api, h, dept)
    url = f"/api/v1/employees/{e['id']}/photo"
    assert (await api.get(url, headers=h)).status_code == 404

    r = await api.put(url, headers=h, files={"file": ("me.jpg", jpeg((1200, 900)), "image/jpeg")})
    assert r.status_code == 200 and r.json()["has_photo"] is True
    files = list(storage_dir.rglob("*.bin"))
    assert len(files) == 1
    assert not files[0].read_bytes().startswith(b"\xff\xd8")  # encrypted at rest

    got = await api.get(url, headers=tokens["VIEWER"])
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(got.content)).size) == 512

    await api.put(url, headers=h, files={"file": ("2.jpg", jpeg(), "image/jpeg")})
    assert len(list(storage_dir.rglob("*.bin"))) == 1  # old file removed

    assert (await api.delete(url, headers=h)).status_code == 204
    assert list(storage_dir.rglob("*.bin")) == []
    assert (await api.get(url, headers=h)).status_code == 404
    assert (await api.delete(url, headers=h)).status_code == 404


async def test_photo_rejects_bad_files(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    e = await create(api, h, dept)
    url = f"/api/v1/employees/{e['id']}/photo"
    # Content type from the client is ignored: a text file named .jpg is still rejected.
    r = await api.put(url, headers=h, files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_FILE"
    small = await api.put(url, headers=h, files={"file": ("s.jpg", jpeg((20, 20)), "image/jpeg")})
    assert small.status_code == 422
    missing = await api.put(url, headers=h)
    assert missing.status_code == 422
    monkeypatch.setattr(get_settings(), "max_photo_bytes", 1024)
    big = await api.put(url, headers=h, files={"file": ("b.jpg", jpeg((900, 900)), "image/jpeg")})
    assert big.status_code == 413


# --- biometric consent --------------------------------------------------------------------


async def test_consent_lifecycle(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    e = await create(api, h, dept)
    base = f"/api/v1/employees/{e['id']}"

    version = (await api.get("/api/v1/biometrics/consent-version", headers=h)).json()
    assert version == {"consent_text_version": "2026-11-v1"}

    wrong = await api.post(
        f"{base}/consents", headers=h, json={"consent_text_version": "old", "employee_signed": True}
    )
    assert wrong.status_code == 422
    unsigned = await api.post(
        f"{base}/consents",
        headers=h,
        json={"consent_text_version": "2026-11-v1", "employee_signed": False},
    )
    assert unsigned.status_code == 422

    c = await api.post(
        f"{base}/consents",
        headers=h,
        json={"consent_text_version": "2026-11-v1", "employee_signed": True},
    )
    assert c.status_code == 201
    cid = c.json()["id"]
    assert c.json()["has_document"] is False and c.json()["is_active"] is True

    emp = (await api.get(base, headers=h)).json()
    assert emp["biometric"]["status"] == "CONSENT_PENDING_DOCUMENT"
    assert emp["biometric"]["can_enroll"] is False

    dup = await api.post(
        f"{base}/consents",
        headers=h,
        json={"consent_text_version": "2026-11-v1", "employee_signed": True},
    )
    assert dup.status_code == 409 and dup.json()["code"] == "CONSENT_ALREADY_ACTIVE"

    bad_doc = await api.put(
        f"{base}/consents/{cid}/document",
        headers=h,
        files={"file": ("x.pdf", b"%PDF-1.4 no end", "application/pdf")},
    )
    assert bad_doc.status_code == 422
    doc = await api.put(
        f"{base}/consents/{cid}/document",
        headers=h,
        files={"file": ("signed.pdf", PDF, "application/pdf")},
    )
    assert doc.status_code == 200 and doc.json()["has_document"] is True
    replace = await api.put(
        f"{base}/consents/{cid}/document",
        headers=h,
        files={"file": ("again.pdf", PDF, "application/pdf")},
    )
    assert replace.status_code == 409 and replace.json()["code"] == "DOCUMENT_ALREADY_ATTACHED"

    emp = (await api.get(base, headers=h)).json()
    assert emp["biometric"]["status"] == "READY_FOR_ENROLLMENT"
    assert emp["biometric"]["consent_documented"] is True and emp["biometric"]["can_enroll"] is True

    dl = await api.get(f"{base}/consents/{cid}/document", headers=h)
    assert dl.status_code == 200 and dl.content == PDF
    assert dl.headers["content-disposition"].startswith("attachment")
    assert "BIOMETRIC_CONSENT_DOCUMENT_VIEWED" in await audit_actions(session)

    # Simulate a future enrollment, then revoke: templates are purged.
    db_emp = await session.get(Employee, uuid.UUID(e["id"]))
    consent_row = await session.get(f.BiometricConsent, uuid.UUID(cid))
    hr = (await session.execute(select(f.User).limit(1))).scalar_one()
    assert db_emp and consent_row
    await f.embedding(session, db_emp, consent_row, hr)
    assert (await api.get(base, headers=h)).json()["biometric"]["status"] == "ENROLLED"

    rv = await api.post(
        f"{base}/consents/{cid}/revoke", headers=h, json={"reason": "Xodim rad etdi"}
    )
    assert rv.status_code == 200 and rv.json()["is_active"] is False
    left = (
        await session.execute(
            select(func.count())
            .select_from(FaceEmbedding)
            .where(FaceEmbedding.employee_id == db_emp.id)
        )
    ).scalar_one()
    assert left == 0
    assert (await api.get(base, headers=h)).json()["biometric"]["status"] == "CONSENT_REVOKED"
    assert (
        await api.post(f"{base}/consents/{cid}/revoke", headers=h, json={"reason": "again"})
    ).status_code == 409

    history = await api.get(f"{base}/consents", headers=h)
    assert history.status_code == 200 and len(history.json()) == 1


async def test_manual_attendance_alternative(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    e = await create(api, h, dept, attendance_method="MANUAL")
    assert e["biometric"]["status"] == "NOT_APPLICABLE" and e["biometric"]["can_enroll"] is False
    r = await api.post(
        f"/api/v1/employees/{e['id']}/consents",
        headers=h,
        json={"consent_text_version": "2026-11-v1", "employee_signed": True},
    )
    assert r.status_code == 409 and r.json()["code"] == "BIOMETRICS_NOT_APPLICABLE"

    # Switching FACE -> MANUAL is blocked while a consent is active (revoke first).
    e2 = await create(api, h, dept)
    await api.post(
        f"/api/v1/employees/{e2['id']}/consents",
        headers=h,
        json={"consent_text_version": "2026-11-v1", "employee_signed": True},
    )
    sw = await api.patch(
        f"/api/v1/employees/{e2['id']}", headers=h, json={"attendance_method": "MANUAL"}
    )
    assert sw.status_code == 409 and sw.json()["code"] == "ACTIVE_CONSENT_EXISTS"


async def test_no_consent_for_terminated(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    e = await create(api, h, dept)
    await api.post(
        f"/api/v1/employees/{e['id']}/deactivate",
        headers=h,
        json={"status": "TERMINATED", "reason": "Ketdi"},
    )
    r = await api.post(
        f"/api/v1/employees/{e['id']}/consents",
        headers=h,
        json={"consent_text_version": "2026-11-v1", "employee_signed": True},
    )
    assert r.status_code == 409 and r.json()["code"] == "EMPLOYEE_TERMINATED"


async def test_consent_of_other_employee_not_reachable(
    api: httpx.AsyncClient,
    tokens: dict,
    dept: uuid.UUID,  # type: ignore[type-arg]
) -> None:
    h = tokens["ADMIN"]
    a, b = await create(api, h, dept), await create(api, h, dept)
    c = await api.post(
        f"/api/v1/employees/{a['id']}/consents",
        headers=h,
        json={"consent_text_version": "2026-11-v1", "employee_signed": True},
    )
    cid = c.json()["id"]
    r = await api.post(
        f"/api/v1/employees/{b['id']}/consents/{cid}/revoke",
        headers=h,
        json={"reason": "wrong person"},
    )
    assert r.status_code == 404


# --- departments --------------------------------------------------------------------------


async def test_departments(api: httpx.AsyncClient, tokens: dict) -> None:  # type: ignore[type-arg]
    h = tokens["ADMIN"]
    root = (await api.post("/api/v1/departments", headers=h, json={"name": "Markaz"})).json()
    child = (
        await api.post(
            "/api/v1/departments", headers=h, json={"name": "IT", "parent_id": root["id"]}
        )
    ).json()
    dup = await api.post(
        "/api/v1/departments", headers=h, json={"name": "it", "parent_id": root["id"]}
    )
    assert dup.status_code == 409

    cycle = await api.patch(
        f"/api/v1/departments/{root['id']}", headers=h, json={"parent_id": child["id"]}
    )
    assert cycle.status_code == 422 and cycle.json()["errors"][0]["code"] == "cycle"

    await create(api, h, uuid.UUID(child["id"]))
    lst = (await api.get("/api/v1/departments", headers=tokens["VIEWER"])).json()
    counts = {d["name"]: d["employee_count"] for d in lst}
    assert counts["IT"] == 1 and counts["Markaz"] == 0

    assert (await api.delete(f"/api/v1/departments/{child['id']}", headers=h)).status_code == 409
    assert (await api.delete(f"/api/v1/departments/{root['id']}", headers=h)).status_code == 409
    empty = (await api.post("/api/v1/departments", headers=h, json={"name": "Bo'sh"})).json()
    assert (await api.delete(f"/api/v1/departments/{empty['id']}", headers=h)).status_code == 204
    assert (
        await api.patch(f"/api/v1/departments/{empty['id']}", headers=h, json={"name": "x"})
    ).status_code == 404
