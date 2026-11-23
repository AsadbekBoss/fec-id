"""Holidays and employee leave (excused days).

* holidays: a date + name, for every branch (branch_id NULL) or one branch; unique per scope.
* employee_leaves: vacation / sick / business trip / other excused range of an employee.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("VACATION", "SICK", "TRIP", "OTHER")


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "holidays",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        *_ts(),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_holidays_name_not_blank")),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"],
                                name=op.f("fk_holidays_branch_id_branches"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name=op.f("fk_holidays_created_by_user_id_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holidays")),
    )
    op.create_index(op.f("ix_holidays_day"), "holidays", ["day"], unique=False)
    op.create_index(op.f("ix_holidays_branch_id"), "holidays", ["branch_id"], unique=False)
    op.create_index(
        "uq_holidays_branch_day", "holidays",
        [sa.literal_column("coalesce(branch_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
         "day"],
        unique=True,
    )
    op.execute(
        "CREATE TRIGGER trg_holidays_updated_at BEFORE UPDATE ON holidays "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.create_table(
        "employee_leaves",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        *_ts(),
        sa.CheckConstraint("kind IN (" + ", ".join(f"'{k}'" for k in KINDS) + ")",
                           name=op.f("ck_employee_leaves_kind_known")),
        sa.CheckConstraint("date_to >= date_from", name=op.f("ck_employee_leaves_range_ordered")),
        sa.CheckConstraint("date_to - date_from < 366",
                           name=op.f("ck_employee_leaves_range_bounded")),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"],
                                name=op.f("fk_employee_leaves_employee_id_employees"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name=op.f("fk_employee_leaves_created_by_user_id_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_leaves")),
    )
    op.create_index("ix_employee_leaves_employee_range", "employee_leaves",
                    ["employee_id", "date_from", "date_to"], unique=False)
    op.execute(
        "CREATE TRIGGER trg_employee_leaves_updated_at BEFORE UPDATE ON employee_leaves "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.drop_table("employee_leaves")
    op.drop_table("holidays")
