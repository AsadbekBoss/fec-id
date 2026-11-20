"""recognition -> attendance master switch (off by default)

* recognition.attendance_enabled = false: recognised faces create attendance events only after the
  owner switches this on (after the recognition evaluation, DEVELOPMENT_PROGRESS D22).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10 13:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS = (
    (
        "recognition.attendance_enabled",
        "false",
        "Master switch: confident recognitions on cameras with recognition enabled create attendance "
        "events. Keep off until the recognition evaluation has been reviewed.",
    ),
)


def upgrade() -> None:
    for key, value, desc in SETTINGS:
        op.execute(
            "INSERT INTO system_settings (key, value, description) VALUES "
            f"('{key}', '{value}'::jsonb, '{desc.replace(chr(39), chr(39) * 2)}') "
            "ON CONFLICT (key) DO NOTHING"
        )


def downgrade() -> None:
    for key, _, _ in SETTINGS:
        op.execute(f"DELETE FROM system_settings WHERE key = '{key}'")
