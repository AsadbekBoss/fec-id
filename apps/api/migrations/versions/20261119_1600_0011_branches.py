"""Branches (filials): every department, employee, work schedule, camera and user belongs to one.

* `branches` table; the main branch "Markaz" has a fixed id and receives every existing row
  (the column default is that id, so nothing is lost and old code paths keep working).
* Codes and names that were unique system-wide are now unique per branch: employee_code,
  department code and sibling names, work schedule name.
* telegram_recipients.branch_id: NULL = the centre (all branches), else one branch's news only.
* Ignore reason OTHER_BRANCH: a camera records attendance only for its own branch's employees.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAIN = "00000000-0000-0000-0000-000000000001"
TABLES = ("departments", "work_schedules", "employees", "users", "cameras")

REASONS = (
    "UNMATCHED", "DEBOUNCED", "REPEATED_DETECTION", "DUPLICATE_IN", "OUT_WITHOUT_IN",
    "OUT_OF_SEQUENCE", "MONITOR_ONLY", "EMPLOYEE_INACTIVE", "NOT_BIOMETRIC", "OUTSIDE_EMPLOYMENT",
    "OTHER_DEPARTMENT",
)
CK = "ck_attendance_events_ignore_reason_known"
SIBLINGS = "coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid)"


def _check(reasons: Sequence[str]) -> str:
    return "ignore_reason IS NULL OR ignore_reason IN (" + ", ".join(f"'{r}'" for r in reasons) + ")"


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_branches_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_branches")),
    )
    op.create_index("uq_branches_name", "branches", [sa.literal_column("lower(name)")],
                    unique=True)
    op.execute(
        "CREATE TRIGGER trg_branches_updated_at BEFORE UPDATE ON branches "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.execute(f"INSERT INTO branches (id, name) VALUES ('{MAIN}', 'Markaz')")

    for t in TABLES:
        op.add_column(t, sa.Column("branch_id", sa.UUID(), nullable=False,
                                   server_default=sa.text(f"'{MAIN}'::uuid")))
        op.create_foreign_key(op.f(f"fk_{t}_branch_id_branches"), t, "branches",
                              ["branch_id"], ["id"], ondelete="RESTRICT")
        op.create_index(op.f(f"ix_{t}_branch_id"), t, ["branch_id"], unique=False)

    # Unique per branch instead of system-wide.
    op.drop_constraint(op.f("uq_employees_employee_code"), "employees", type_="unique")
    op.create_unique_constraint(op.f("uq_employees_branch_id_employee_code"), "employees",
                                ["branch_id", "employee_code"])
    op.drop_constraint(op.f("uq_departments_code"), "departments", type_="unique")
    op.create_unique_constraint(op.f("uq_departments_branch_id_code"), "departments",
                                ["branch_id", "code"])
    op.drop_constraint(op.f("uq_work_schedules_name"), "work_schedules", type_="unique")
    op.create_unique_constraint(op.f("uq_work_schedules_branch_id_name"), "work_schedules",
                                ["branch_id", "name"])
    op.drop_index("uq_departments_parent_name_active", table_name="departments")
    op.create_index(
        "uq_departments_parent_name_active", "departments",
        ["branch_id", sa.literal_column(SIBLINGS), sa.literal_column("lower(name)")],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.add_column("telegram_recipients", sa.Column("branch_id", sa.UUID(), nullable=True))
    op.create_foreign_key(op.f("fk_telegram_recipients_branch_id_branches"),
                          "telegram_recipients", "branches", ["branch_id"], ["id"],
                          ondelete="CASCADE")
    op.create_index(op.f("ix_telegram_recipients_branch_id"), "telegram_recipients",
                    ["branch_id"], unique=False)

    op.drop_constraint(op.f(CK), "attendance_events", type_="check")
    op.create_check_constraint(op.f(CK), "attendance_events", _check((*REASONS, "OTHER_BRANCH")))


def downgrade() -> None:
    # Codes may now repeat across branches and events are immutable: refuse instead of guessing.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM branches WHERE id <> '" + MAIN + "') THEN RAISE EXCEPTION "
        "'Cannot downgrade 0011: delete or merge the other branches first'; END IF; "
        "IF EXISTS (SELECT 1 FROM attendance_events WHERE ignore_reason = 'OTHER_BRANCH') THEN "
        "RAISE EXCEPTION 'Cannot downgrade 0011: OTHER_BRANCH events exist'; END IF; "
        "END $$"
    )
    op.drop_constraint(op.f(CK), "attendance_events", type_="check")
    op.create_check_constraint(op.f(CK), "attendance_events", _check(REASONS))

    op.drop_index(op.f("ix_telegram_recipients_branch_id"), table_name="telegram_recipients")
    op.drop_constraint(op.f("fk_telegram_recipients_branch_id_branches"),
                       "telegram_recipients", type_="foreignkey")
    op.drop_column("telegram_recipients", "branch_id")

    op.drop_index("uq_departments_parent_name_active", table_name="departments")
    op.create_index(
        "uq_departments_parent_name_active", "departments",
        [sa.literal_column(SIBLINGS), sa.literal_column("lower(name)")],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_constraint(op.f("uq_work_schedules_branch_id_name"), "work_schedules",
                       type_="unique")
    op.create_unique_constraint(op.f("uq_work_schedules_name"), "work_schedules", ["name"])
    op.drop_constraint(op.f("uq_departments_branch_id_code"), "departments", type_="unique")
    op.create_unique_constraint(op.f("uq_departments_code"), "departments", ["code"])
    op.drop_constraint(op.f("uq_employees_branch_id_employee_code"), "employees",
                       type_="unique")
    op.create_unique_constraint(op.f("uq_employees_employee_code"), "employees",
                                ["employee_code"])

    for t in reversed(TABLES):
        op.drop_index(op.f(f"ix_{t}_branch_id"), table_name=t)
        op.drop_constraint(op.f(f"fk_{t}_branch_id_branches"), t, type_="foreignkey")
        op.drop_column(t, "branch_id")
    op.drop_table("branches")
