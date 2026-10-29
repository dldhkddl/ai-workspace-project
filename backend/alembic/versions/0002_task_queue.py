"""add PostgreSQL task queue"""
from alembic import op
import sqlalchemy as sa

revision = "0002_task_queue"
down_revision = "0001_initial_office_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("employee_id", sa.String(length=120), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(length=120)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','cancel_requested','cancelled','completed','failed')", name="ck_task_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_task_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_task_max_attempts"),
    )
    op.create_index("ix_tasks_status_created_at", "tasks", ["status", "created_at"])
    op.create_index("ix_tasks_employee_id", "tasks", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_employee_id", table_name="tasks")
    op.drop_index("ix_tasks_status_created_at", table_name="tasks")
    op.drop_table("tasks")
