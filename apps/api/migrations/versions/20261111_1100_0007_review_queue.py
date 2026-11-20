"""review queue for uncertain recognitions

* attendance_events: review_status (PENDING / ASSIGNED / DISMISSED), reviewed_at,
  reviewed_by_user_id, review_employee_id, review_event_id (+ CHECKs, partial index).
* Only CAMERA events with decision AMBIGUOUS / UNKNOWN can be reviewed. ASSIGNED requires the
  employee and the MANUAL punch that was created. The face crop (snapshot_key) is deleted when
  the review is decided or expires.
* The immutability guard of revision 0004 protects a fixed column list; these new columns are
  intentionally mutable (a review is decided after the event was stored).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

T = "attendance_events"


def upgrade() -> None:
    op.add_column(T, sa.Column("review_status", sa.String(length=16), nullable=True))
    op.add_column(T, sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(T, sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True))
    op.add_column(T, sa.Column("review_employee_id", sa.UUID(), nullable=True))
    op.add_column(T, sa.Column("review_event_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_attendance_events_reviewed_by_user_id_users"), T, "users",
                          ["reviewed_by_user_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key(op.f("fk_attendance_events_review_employee_id_employees"), T,
                          "employees", ["review_employee_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key(op.f("fk_attendance_events_review_event_id_attendance_events"), T, T,
                          ["review_event_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint(
        op.f("ck_attendance_events_review_only_uncertain"), T,
        "review_status IS NULL OR (review_status IN ('PENDING', 'ASSIGNED', 'DISMISSED') "
        "AND source = 'CAMERA' AND decision IN ('AMBIGUOUS', 'UNKNOWN'))",
    )
    op.create_check_constraint(
        op.f("ck_attendance_events_review_assigned_complete"), T,
        "review_status IS DISTINCT FROM 'ASSIGNED' OR "
        "(review_employee_id IS NOT NULL AND review_event_id IS NOT NULL)",
    )
    op.create_index("ix_attendance_events_review_pending", T, ["event_time"],
                    postgresql_where=sa.text("review_status = 'PENDING'"))
    op.execute(
        "INSERT INTO system_settings (key, value, description) VALUES "
        "('recognition.review_enabled', 'true'::jsonb, 'Store a face crop of uncertain "
        "(AMBIGUOUS) recognitions for review; UNKNOWN faces only with "
        "recognition.store_unknown_snapshots. Crops are deleted after the decision or after "
        "retention.unknown_snapshot_days.') ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM system_settings WHERE key = 'recognition.review_enabled'")
    op.drop_index("ix_attendance_events_review_pending", table_name=T)
    for name in ("review_assigned_complete", "review_only_uncertain"):
        op.drop_constraint(op.f(f"ck_attendance_events_{name}"), T, type_="check")
    for fk in ("review_event_id_attendance_events", "review_employee_id_employees",
               "reviewed_by_user_id_users"):
        op.drop_constraint(op.f(f"fk_attendance_events_{fk}"), T, type_="foreignkey")
    for col in ("review_event_id", "review_employee_id", "reviewed_by_user_id", "reviewed_at",
                "review_status"):
        op.drop_column(T, col)
