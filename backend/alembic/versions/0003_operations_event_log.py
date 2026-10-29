"""add durable operations event log"""
from alembic import op
import sqlalchemy as sa

revision = "0003_operations_event_log"
down_revision = "0002_task_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("employee_id", sa.String(length=120), sa.ForeignKey("employees.id", ondelete="SET NULL")),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_activities_created_at", "activities", ["created_at"])
    op.create_index("ix_activities_employee_created_at", "activities", ["employee_id", "created_at"])
    op.create_index("ix_activities_task_id", "activities", ["task_id"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("employee_id", sa.String(length=120), sa.ForeignKey("employees.id", ondelete="SET NULL")),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=36)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('info','warning','error')", name="ck_alert_severity"),
    )
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.create_index("ix_alerts_employee_resolved", "alerts", ["employee_id", "resolved_at"])
    op.create_index("ix_alerts_task_id", "alerts", ["task_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])

    op.create_table(
        "realtime_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_realtime_events_created_at", "realtime_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_realtime_events_created_at", table_name="realtime_events")
    op.drop_table("realtime_events")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_alerts_task_id", table_name="alerts")
    op.drop_index("ix_alerts_employee_resolved", table_name="alerts")
    op.drop_index("ix_alerts_created_at", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_activities_task_id", table_name="activities")
    op.drop_index("ix_activities_employee_created_at", table_name="activities")
    op.drop_index("ix_activities_created_at", table_name="activities")
    op.drop_table("activities")
