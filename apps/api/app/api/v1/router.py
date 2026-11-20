"""Aggregates all /api/v1 routers."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.api.v1 import (
    attendance,
    auth,
    branches,
    cameras,
    departments,
    employees,
    face_templates,
    notifications,
    reports,
    reviews,
    settings,
    system,
    users,
    work_schedules,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(branches.router)
api_router.include_router(settings.router)
api_router.include_router(departments.router)
api_router.include_router(employees.router)
api_router.include_router(employees.biometrics_router)
api_router.include_router(work_schedules.router)
api_router.include_router(attendance.router)
api_router.include_router(cameras.router)
api_router.include_router(face_templates.router)
api_router.include_router(reports.router)
api_router.include_router(reviews.router)
api_router.include_router(system.router)
api_router.include_router(notifications.router)
api_router.include_router(notifications.employee_router)


class VersionResponse(BaseModel):
    api: str


# Public by design (API_CONTRACTS §2.11).
@api_router.get("/system/version", response_model=VersionResponse, tags=["system"])
async def version() -> VersionResponse:
    return VersionResponse(api=__version__)
