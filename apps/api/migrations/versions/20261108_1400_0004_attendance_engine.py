"""attendance engine: department schedules, event classification, immutable events

* departments.work_schedule_id: default schedule for a department subtree.
* attendance_events: ignore_reason, correction_id, voided_at, voided_by_user_id + CHECKs, and a
  partial unique index (employee_id, event_time) on counted, non-voided events.
* Events are immutable: only void fields (and snapshot_key, for retention) may change. Voiding
  cannot be undone, and rows cannot be deleted. Corrections therefore leave a full trail.
* Settings for the engine.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08 14:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IGNORE_REASONS = (
    'UNMATCHED', 'DEBOUNCED', 'REPEATED_DETECTION', 'DUPLICATE_IN', 'OUT_WITHOUT_IN',
    'OUT_OF_SEQUENCE', 'MONITOR_ONLY', 'EMPLOYEE_INACTIVE', 'NOT_BIOMETRIC', 'OUTSIDE_EMPLOYMENT',
)

PROTECTED = (
    'id', 'event_uid', 'event_time', 'source', 'employee_id', 'camera_id', 'decision',
    'direction', 'work_date', 'counts_for_attendance', 'similarity', 'second_similarity',
    'liveness_score', 'quality_score', 'track_id', 'face_bbox', 'model_id',
    'created_by_user_id', 'note', 'created_at', 'ignore_reason', 'correction_id',
)

GUARD_FN = (
    "CREATE OR REPLACE FUNCTION attendance_events_guard() RETURNS trigger\n"
    "LANGUAGE plpgsql AS $$\n"
    "BEGIN\n"
    "    IF TG_OP = 'DELETE' THEN\n"
    "        RAISE EXCEPTION 'attendance_events rows cannot be deleted; void them instead'\n"
    "            USING ERRCODE = 'restrict_violation';\n"
    "    END IF;\n"
    "    IF OLD.is_voided AND NOT NEW.is_voided THEN\n"
    "        RAISE EXCEPTION 'a voided attendance event cannot be restored'\n"
    "            USING ERRCODE = 'restrict_violation';\n"
    "    END IF;\n"
    "    IF " + "\n       OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in PROTECTED) + " THEN\n"
    "        RAISE EXCEPTION 'attendance events are immutable (only voiding is allowed)'\n"
    "            USING ERRCODE = 'restrict_violation';\n"
    "    END IF;\n"
    "    RETURN NEW;\n"
    "END $$"
)

SETTINGS = (
    ("attendance.min_presence_minutes", "30",
     "BOTH-direction cameras: a sighting sooner than this after IN is a repeated detection, not a departure."),
    ("attendance.max_event_age_hours", "168",
     "Camera events older than this are rejected (clock or replay problem)."),
    ("attendance.max_clock_skew_seconds", "120",
     "Events timestamped further in the future than this are rejected."),
    ("attendance.default_timezone", '"Asia/Tashkent"',
     "Time zone for employees without a work schedule."),
)


def upgrade() -> None:
    op.add_column('departments', sa.Column('work_schedule_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_departments_work_schedule_id_work_schedules'), 'departments',
                          'work_schedules', ['work_schedule_id'], ['id'], ondelete='RESTRICT')
    op.create_index(op.f('ix_departments_work_schedule_id'), 'departments', ['work_schedule_id'])

    op.add_column('attendance_events', sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('attendance_events', sa.Column('voided_by_user_id', sa.UUID(), nullable=True))
    op.add_column('attendance_events', sa.Column('ignore_reason', sa.String(length=32), nullable=True))
    op.add_column('attendance_events', sa.Column('correction_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_attendance_events_voided_by_user_id_users'), 'attendance_events',
                          'users', ['voided_by_user_id'], ['id'], ondelete='RESTRICT')
    op.create_index(op.f('ix_attendance_events_correction_id'), 'attendance_events', ['correction_id'])

    # Backfill existing rows so the new CHECKs hold (pre-0004 data: dev only).
    op.execute("UPDATE attendance_events SET voided_at = created_at, "
               "voided_by_user_id = created_by_user_id WHERE is_voided")
    op.execute("UPDATE attendance_events SET ignore_reason = 'UNMATCHED' "
               "WHERE NOT counts_for_attendance AND ignore_reason IS NULL")
    op.execute("UPDATE attendance_events SET correction_id = gen_random_uuid() "
               "WHERE source = 'MANUAL' AND correction_id IS NULL")

    op.create_check_constraint(
        op.f('ck_attendance_events_void_fields_together'), 'attendance_events',
        "is_voided = (voided_at IS NOT NULL) AND (voided_at IS NULL) = (voided_by_user_id IS NULL)",
    )
    op.create_check_constraint(
        op.f('ck_attendance_events_ignore_reason_iff_not_counted'), 'attendance_events',
        "(counts_for_attendance AND ignore_reason IS NULL) OR "
        "(NOT counts_for_attendance AND ignore_reason IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f('ck_attendance_events_ignore_reason_known'), 'attendance_events',
        "ignore_reason IS NULL OR ignore_reason IN (" + ", ".join(f"'{r}'" for r in IGNORE_REASONS) + ")",
    )
    op.create_check_constraint(
        op.f('ck_attendance_events_manual_has_correction'), 'attendance_events',
        "source <> 'MANUAL' OR correction_id IS NOT NULL",
    )
    op.create_index('uq_attendance_events_counted_instant', 'attendance_events',
                    ['employee_id', 'event_time'], unique=True,
                    postgresql_where=sa.text('counts_for_attendance AND NOT is_voided'))

    op.execute(GUARD_FN)
    op.execute("CREATE TRIGGER trg_attendance_events_guard BEFORE UPDATE OR DELETE "
               "ON attendance_events FOR EACH ROW EXECUTE FUNCTION attendance_events_guard()")

    for key, value, desc in SETTINGS:
        op.execute(
            "INSERT INTO system_settings (key, value, description) VALUES "
            f"('{key}', '{value}'::jsonb, '{desc.replace(chr(39), chr(39) * 2)}') "
            "ON CONFLICT (key) DO NOTHING"
        )


def downgrade() -> None:
    for key, _, _ in SETTINGS:
        op.execute(f"DELETE FROM system_settings WHERE key = '{key}'")
    op.execute("DROP TRIGGER IF EXISTS trg_attendance_events_guard ON attendance_events")
    op.execute("DROP FUNCTION IF EXISTS attendance_events_guard()")
    op.drop_index('uq_attendance_events_counted_instant', table_name='attendance_events',
                  postgresql_where=sa.text('counts_for_attendance AND NOT is_voided'))
    for name in ('manual_has_correction', 'ignore_reason_known', 'ignore_reason_iff_not_counted',
                 'void_fields_together'):
        op.drop_constraint(op.f(f'ck_attendance_events_{name}'), 'attendance_events', type_='check')
    op.drop_index(op.f('ix_attendance_events_correction_id'), table_name='attendance_events')
    op.drop_constraint(op.f('fk_attendance_events_voided_by_user_id_users'), 'attendance_events',
                       type_='foreignkey')
    for col in ('correction_id', 'ignore_reason', 'voided_by_user_id', 'voided_at'):
        op.drop_column('attendance_events', col)
    op.drop_index(op.f('ix_departments_work_schedule_id'), table_name='departments')
    op.drop_constraint(op.f('fk_departments_work_schedule_id_work_schedules'), 'departments',
                       type_='foreignkey')
    op.drop_column('departments', 'work_schedule_id')
