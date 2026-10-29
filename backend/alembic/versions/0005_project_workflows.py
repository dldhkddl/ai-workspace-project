"""PM 계획 자동 배분 관계 추가.

Revision ID: 0005_project_workflows
Revises: 0004_projects_and_approvals
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_project_workflows"
down_revision = "0004_projects_and_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("parent_task_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_parent_task_id_tasks", "tasks", ["parent_task_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_index("ix_tasks_parent_task_id", ["parent_task_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_parent_task_id")
        batch.drop_constraint("fk_tasks_parent_task_id_tasks", type_="foreignkey")
        batch.drop_column("parent_task_id")
