"""initial office schema"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_office_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("short_name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=30), nullable=False),
    )
    op.create_table(
        "employees",
        sa.Column("id", sa.String(length=120), primary_key=True),
        sa.Column("team_id", sa.String(length=80), sa.ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=160), nullable=False),
        sa.Column("icon", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("planned_source", sa.String(length=160), nullable=False),
        sa.Column("planned_features", sa.JSON(), nullable=False),
    )
    op.create_index("ix_employees_team_id", "employees", ["team_id"])
    op.create_table(
        "employee_connections",
        sa.Column("employee_id", sa.String(length=120), sa.ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=160)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.JSON()),
        sa.CheckConstraint("status IN ('not_connected','setup_required','awaiting_first_sync','healthy','delayed','error','stopped')", name="ck_connection_status"),
    )
    op.create_index("ix_connections_status", "employee_connections", ["status"])
    op.create_table(
        "employee_work_states",
        sa.Column("employee_id", sa.String(length=120), sa.ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(length=40)),
        sa.Column("task_id", sa.String(length=120)),
        sa.Column("title", sa.String(length=200)),
        sa.Column("stage", sa.String(length=120)),
        sa.Column("progress", sa.Integer()),
        sa.CheckConstraint("status IS NULL OR status IN ('idle','working','reviewing','awaiting_approval','completed','failed')", name="ck_work_status"),
    )


def downgrade() -> None:
    op.drop_table("employee_work_states")
    op.drop_index("ix_connections_status", table_name="employee_connections")
    op.drop_table("employee_connections")
    op.drop_index("ix_employees_team_id", table_name="employees")
    op.drop_table("employees")
    op.drop_table("teams")
