"""camera integration: providers (RTSP / USB / TEST), optional credentials, quality status, runtime

* cameras.provider (existing rows are RTSP), cameras.device (USB index or /dev/videoN).
* host / main_stream_path / credentials become optional, guarded by ck_cameras_provider_fields:
  RTSP needs host + main path; USB needs a device; only RTSP may hold credentials. Credentials
  are both-or-neither and always ciphertext.
* quality_status (UNTESTED by default), quality_tested_at, quality_report: a stream is never
  assumed suitable for face recognition until tested.
* runtime / runtime_reported_at: last status report from the camera service.

Downgrade refuses to run while USB/TEST cameras or credential-less RTSP cameras exist (it cannot
restore the old NOT NULL columns for them).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER = postgresql.ENUM("RTSP", "USB", "TEST", name="camera_provider")
QUALITY = postgresql.ENUM(
    "UNTESTED", "PASSED", "FAILED", "INCONCLUSIVE", name="camera_quality_status"
)

OLD_CREDENTIALS = "octet_length(username_enc) >= 30 AND octet_length(password_enc) >= 30"
OLD_PATHS = (
    "position('@' in main_stream_path) = 0 AND position('://' in main_stream_path) = 0 "
    "AND (sub_stream_path IS NULL OR (position('@' in sub_stream_path) = 0 "
    "AND position('://' in sub_stream_path) = 0))"
)
OLD_HOST = "host ~ '^[A-Za-z0-9.:-]{1,255}$'"


def upgrade() -> None:
    bind = op.get_bind()
    PROVIDER.create(bind)
    QUALITY.create(bind)
    op.add_column("cameras", sa.Column("provider", PROVIDER, server_default="RTSP", nullable=False))
    op.add_column("cameras", sa.Column("device", sa.String(length=32), nullable=True))
    op.add_column(
        "cameras", sa.Column("runtime", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "cameras", sa.Column("runtime_reported_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "cameras", sa.Column("quality_status", QUALITY, server_default="UNTESTED", nullable=False)
    )
    op.add_column(
        "cameras", sa.Column("quality_tested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "cameras",
        sa.Column("quality_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    for col in ("host", "main_stream_path", "username_enc", "password_enc"):
        op.alter_column("cameras", col, nullable=True)

    for name in ("credentials_encrypted", "stream_paths_no_credentials", "host_format"):
        op.drop_constraint(op.f(f"ck_cameras_{name}"), "cameras", type_="check")
    op.create_check_constraint(
        op.f("ck_cameras_host_format"),
        "cameras",
        "host IS NULL OR host ~ '^[A-Za-z0-9.:-]{1,255}$'",
    )
    op.create_check_constraint(
        op.f("ck_cameras_stream_paths_no_credentials"),
        "cameras",
        "(main_stream_path IS NULL OR (position('@' in main_stream_path) = 0 "
        "AND position('://' in main_stream_path) = 0)) "
        "AND (sub_stream_path IS NULL OR (position('@' in sub_stream_path) = 0 "
        "AND position('://' in sub_stream_path) = 0))",
    )
    op.create_check_constraint(
        op.f("ck_cameras_credentials_encrypted"),
        "cameras",
        "(username_enc IS NULL) = (password_enc IS NULL) AND (username_enc IS NULL OR "
        "(octet_length(username_enc) >= 30 AND octet_length(password_enc) >= 30))",
    )
    op.create_check_constraint(
        op.f("ck_cameras_provider_fields"),
        "cameras",
        "(provider <> 'RTSP' OR (host IS NOT NULL AND main_stream_path IS NOT NULL)) "
        "AND (provider <> 'USB' OR device IS NOT NULL) "
        "AND (provider = 'RTSP' OR username_enc IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_cameras_device_format"),
        "cameras",
        "device IS NULL OR device ~ '^([0-9]{1,2}|/dev/video[0-9]{1,2})$'",
    )


def downgrade() -> None:
    blocked = (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM cameras WHERE provider <> 'RTSP' OR username_enc IS NULL")
        )
        .scalar_one()
    )
    if blocked:
        raise RuntimeError(
            f"{blocked} camera(s) are USB/TEST or have no credentials; remove them before downgrading 0005"
        )
    for name in (
        "provider_fields",
        "device_format",
        "credentials_encrypted",
        "stream_paths_no_credentials",
        "host_format",
    ):
        op.drop_constraint(op.f(f"ck_cameras_{name}"), "cameras", type_="check")
    op.create_check_constraint(op.f("ck_cameras_host_format"), "cameras", OLD_HOST)
    op.create_check_constraint(op.f("ck_cameras_stream_paths_no_credentials"), "cameras", OLD_PATHS)
    op.create_check_constraint(op.f("ck_cameras_credentials_encrypted"), "cameras", OLD_CREDENTIALS)
    for col in ("host", "main_stream_path", "username_enc", "password_enc"):
        op.alter_column("cameras", col, nullable=False)
    for col in (
        "quality_report",
        "quality_tested_at",
        "quality_status",
        "runtime_reported_at",
        "runtime",
        "device",
        "provider",
    ):
        op.drop_column("cameras", col)
    bind = op.get_bind()
    QUALITY.drop(bind)
    PROVIDER.drop(bind)
