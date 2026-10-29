"""add projects and commit-bound approvals"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_projects_and_approvals"
down_revision = "0003_operations_event_log"
branch_labels = None
depends_on = None


def json_column() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("repository_root", sa.String(length=500), nullable=False),
        sa.Column("project_path", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("dependency_paths", json_column(), nullable=False),
        # 셸 문자열이 아니라 구조화된 정책만 담는다. 1차에서는 {"version":1,"enabled":false}.
        sa.Column("verification_policy", json_column(), nullable=False),
        sa.Column("verification_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','paused')", name="ck_project_status"),
        sa.UniqueConstraint("repository_root", "project_path", name="uq_projects_repository_path"),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=80), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=False),
        sa.Column("result_sha", sa.String(length=40), nullable=False),
        sa.Column("result_tree", sa.String(length=40), nullable=False),
        sa.Column("impact_hash", sa.String(length=64), nullable=False),
        sa.Column("impact_hash_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("impact_scope", json_column(), nullable=False),
        sa.Column("impact_scope_hash", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("target_branch", sa.String(length=200)),
        sa.Column("target_sha_at_approval", sa.String(length=40)),
        sa.Column("verification_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("has_sensitive_changes", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sensitive_files", json_column(), nullable=False),
        sa.Column("patch_path", sa.String(length=500)),
        sa.Column("patch_sha256", sa.String(length=64)),
        sa.Column("actor_id", sa.String(length=120)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("applied_sha", sa.String(length=40)),
        sa.Column("invalidated_reason", sa.String(length=120)),
        sa.Column("last_apply_error", sa.String(length=400)),
        sa.Column("apply_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correlation_id", sa.String(length=36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','approved','applying','blocked','applied',"
            "'rejected','invalidated','expired')",
            name="ck_approval_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_approval_revision"),
        sa.UniqueConstraint(
            "task_id", "result_sha", "revision", name="uq_approvals_task_result_revision"
        ),
    )
    op.create_index("ix_approvals_project_status", "approvals", ["project_id", "status"])
    op.create_index("ix_approvals_thread_id", "approvals", ["thread_id"])

    # 개발 작업은 만들어지는 순간 프로젝트에 묶인다. 기존 분석 작업은 NULL 이다.
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("project_id", sa.String(length=80), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_project_id", "projects", ["project_id"], ["id"], ondelete="RESTRICT"
        )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])

    # 인증이 붙기 전이라도 누가 승인했는지 자리는 지금 만들어 둔다.
    op.add_column("audit_logs", sa.Column("actor_id", sa.String(length=120), nullable=True))
    # 같은 결과에 살아 있는 승인은 하나만. SQLite·PostgreSQL 모두 부분 유니크 인덱스를 지원한다.
    active = sa.text("status IN ('pending','approved','applying','blocked')")
    op.create_index(
        "uq_approvals_active_result",
        "approvals",
        ["task_id", "result_sha"],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "actor_id")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_project_id", type_="foreignkey")
        batch.drop_column("project_id")
    op.drop_index("uq_approvals_active_result", table_name="approvals")
    op.drop_index("ix_approvals_thread_id", table_name="approvals")
    op.drop_index("ix_approvals_project_status", table_name="approvals")
    op.drop_table("approvals")
    op.drop_table("projects")
