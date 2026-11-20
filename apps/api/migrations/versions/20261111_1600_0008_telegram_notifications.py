"""Telegram notifications: recipients, send-once log, settings.

* telegram_recipients: chats linked with a one-time code (person or group), with the kinds of
  messages they want (daily summary, late arrivals, review queue, system alerts).
* notification_log: (kind, key) unique; a row is inserted before sending so nothing is sent twice.
* Settings: master switch OFF by default (sending personal data to Telegram is an explicit
  decision), daily summary time, review reminder interval, camera offline threshold.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS = [
    ("notify.telegram_enabled", "false",
     "Send Telegram notifications (names and times leave the server). The bot token is set in "
     "apps/api/.env (TELEGRAM_BOT_TOKEN), never in the database."),
    ("notify.daily_summary_time", '"10:00"',
     "Local time (attendance.default_timezone) of the daily attendance summary, HH:MM."),
    ("notify.review_interval_minutes", "15",
     "At most one 'faces waiting for review' message per this many minutes."),
    ("notify.camera_offline_minutes", "5",
     "Alert when an enabled camera has delivered no frame for this many minutes."),
]


def upgrade() -> None:
    op.create_table(
        "telegram_recipients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("subscriptions", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("""'["daily", "reviews", "system"]'::jsonb"""),
                  nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.CheckConstraint("jsonb_typeof(subscriptions) = 'array'",
                           name=op.f("ck_telegram_recipients_subscriptions_is_array")),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name=op.f("fk_telegram_recipients_created_by_user_id_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_recipients")),
        sa.UniqueConstraint("chat_id", name=op.f("uq_telegram_recipients_chat_id")),
    )
    op.execute(
        "CREATE TRIGGER trg_telegram_recipients_updated_at BEFORE UPDATE ON telegram_recipients "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.create_table(
        "notification_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("recipients", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_log")),
        sa.UniqueConstraint("kind", "key", name=op.f("uq_notification_log_kind_key")),
    )
    for key, value, description in SETTINGS:  # constants: literal SQL works offline too
        op.execute(
            "INSERT INTO system_settings (key, value, description) VALUES "
            f"('{key}', '{value}'::jsonb, '{description.replace(chr(39), chr(39) * 2)}') "
            "ON CONFLICT (key) DO NOTHING"
        )


def downgrade() -> None:
    keys = ", ".join(f"'{k}'" for k, _, _ in SETTINGS)
    op.execute(f"DELETE FROM system_settings WHERE key IN ({keys})")
    op.drop_table("notification_log")
    op.drop_table("telegram_recipients")
