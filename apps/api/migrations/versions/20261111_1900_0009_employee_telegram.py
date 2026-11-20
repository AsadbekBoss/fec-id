"""Telegram chats linked to employees (their own arrival / departure / lateness messages).

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

T = "telegram_recipients"


def upgrade() -> None:
    op.add_column(T, sa.Column("employee_id", sa.UUID(), nullable=True))
    op.create_foreign_key(op.f("fk_telegram_recipients_employee_id_employees"), T, "employees",
                          ["employee_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint(op.f("uq_telegram_recipients_employee_id"), T, ["employee_id"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_telegram_recipients_employee_id"), T, type_="unique")
    op.drop_constraint(op.f("fk_telegram_recipients_employee_id_employees"), T,
                       type_="foreignkey")
    op.drop_column(T, "employee_id")
