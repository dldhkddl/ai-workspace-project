"""AI 스토리 제작팀과 연동 대기 직원을 추가한다.

Revision ID: 0006_story_production_team
Revises: 0005_project_workflows
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_story_production_team"
down_revision = "0005_project_workflows"
branch_labels = None
depends_on = None


teams = sa.table(
    "teams",
    sa.column("id", sa.String),
    sa.column("name", sa.String),
    sa.column("short_name", sa.String),
    sa.column("color", sa.String),
)
employees = sa.table(
    "employees",
    sa.column("id", sa.String),
    sa.column("team_id", sa.String),
    sa.column("name", sa.String),
    sa.column("role", sa.String),
    sa.column("icon", sa.String),
    sa.column("summary", sa.Text),
    sa.column("allowed_tools", sa.JSON),
    sa.column("approval_required", sa.Boolean),
    sa.column("planned_source", sa.String),
    sa.column("planned_features", sa.JSON),
)
connections = sa.table(
    "employee_connections",
    sa.column("employee_id", sa.String),
    sa.column("status", sa.String),
    sa.column("source", sa.String),
    sa.column("last_checked_at", sa.DateTime(timezone=True)),
    sa.column("last_success_at", sa.DateTime(timezone=True)),
    sa.column("error", sa.JSON),
)
work_states = sa.table(
    "employee_work_states",
    sa.column("employee_id", sa.String),
    sa.column("status", sa.String),
    sa.column("task_id", sa.String),
    sa.column("title", sa.String),
    sa.column("stage", sa.String),
    sa.column("progress", sa.Integer),
)


NEW_EMPLOYEES = [
    {
        "id": "scenario-writer",
        "team_id": "story-production",
        "name": "시나리오 작가",
        "role": "주제·구성·장면별 시나리오 작성",
        "icon": "ScrollText",
        "summary": None,
        "allowed_tools": [],
        "approval_required": True,
        "planned_source": "스토리 제작 작업 공간",
        "planned_features": ["시나리오 기획", "장면 구성"],
    },
    {
        "id": "story-editor",
        "team_id": "story-production",
        "name": "스토리 에디터",
        "role": "이야기 흐름·몰입도·일관성 편집",
        "icon": "BookOpenCheck",
        "summary": None,
        "allowed_tools": [],
        "approval_required": True,
        "planned_source": "스토리 제작 작업 공간",
        "planned_features": ["구성 편집", "일관성 검토"],
    },
    {
        "id": "prompt-director",
        "team_id": "story-production",
        "name": "프롬프트 감독",
        "role": "이미지·영상 생성 프롬프트 설계와 품질 통제",
        "icon": "WandSparkles",
        "summary": None,
        "allowed_tools": [],
        "approval_required": True,
        "planned_source": "생성형 콘텐츠 작업 공간",
        "planned_features": ["프롬프트 설계", "생성 품질 검토"],
    },
]


def upgrade() -> None:
    op.bulk_insert(teams, [{
        "id": "story-production",
        "name": "AI 스토리 제작팀",
        "short_name": "스토리팀",
        "color": "#9333ea",
    }])
    op.execute(
        employees.update()
        .where(employees.c.id == "shorts-producer")
        .values(team_id="story-production", name="숏폼 제작")
    )
    op.bulk_insert(employees, NEW_EMPLOYEES)
    op.bulk_insert(connections, [
        {"employee_id": row["id"], "status": "not_connected", "source": None,
         "last_checked_at": None, "last_success_at": None, "error": None}
        for row in NEW_EMPLOYEES
    ])
    op.bulk_insert(work_states, [
        {"employee_id": row["id"], "status": None, "task_id": None,
         "title": None, "stage": None, "progress": None}
        for row in NEW_EMPLOYEES
    ])


def downgrade() -> None:
    employee_ids = [row["id"] for row in NEW_EMPLOYEES]
    op.execute(work_states.delete().where(work_states.c.employee_id.in_(employee_ids)))
    op.execute(connections.delete().where(connections.c.employee_id.in_(employee_ids)))
    op.execute(employees.delete().where(employees.c.id.in_(employee_ids)))
    op.execute(
        employees.update()
        .where(employees.c.id == "shorts-producer")
        .values(team_id="operations")
    )
    op.execute(teams.delete().where(teams.c.id == "story-production"))
