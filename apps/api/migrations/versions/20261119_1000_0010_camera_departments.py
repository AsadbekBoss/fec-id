"""Cameras serve departments: cameras.department_ids + ignore reason OTHER_DEPARTMENT.

A camera with departments still matches faces against every employee (an outsider is identified
as who they are, never mistaken for a member); a recognised employee of another department is
stored with ignore_reason OTHER_DEPARTMENT and does not count for attendance.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASE = (
    "UNMATCHED", "DEBOUNCED", "REPEATED_DETECTION", "DUPLICATE_IN", "OUT_WITHOUT_IN",
    "OUT_OF_SEQUENCE", "MONITOR_ONLY", "EMPLOYEE_INACTIVE", "NOT_BIOMETRIC", "OUTSIDE_EMPLOYMENT",
)
NAME = "ck_attendance_events_ignore_reason_known"


def _check(reasons: Sequence[str]) -> str:
    return "ignore_reason IS NULL OR ignore_reason IN (" + ", ".join(f"'{r}'" for r in reasons) + ")"


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("department_ids", postgresql.ARRAY(sa.UUID()), server_default="{}",
                  nullable=False),
    )
    op.drop_constraint(op.f(NAME), "attendance_events", type_="check")
    op.create_check_constraint(op.f(NAME), "attendance_events", _check((*BASE, "OTHER_DEPARTMENT")))


def downgrade() -> None:
    # Events are immutable (revision 0004), so they cannot be relabelled: refuse instead.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM attendance_events "
        "WHERE ignore_reason = 'OTHER_DEPARTMENT') THEN RAISE EXCEPTION "
        "'Cannot downgrade 0010: OTHER_DEPARTMENT events exist (attendance events are immutable)'; "
        "END IF; END $$"
    )
    op.drop_constraint(op.f(NAME), "attendance_events", type_="check")
    op.create_check_constraint(op.f(NAME), "attendance_events", _check(BASE))
    op.drop_column("cameras", "department_ids")
