from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.models import Base, EmployeeConnection
from app.seed import seed_database


DEVELOPMENT_EMPLOYEE = "frontend"
ANALYSIS_EMPLOYEE = "research-factcheck"


def employee_task_body(employee_id: str) -> dict:
    return {
        "employee_id": employee_id,
        "task_type": "employee_task",
        "title": "작업 요청",
        "payload": {"request": "요청 내용을 처리해줘"},
    }


def request_task(client, employee_id: str, key: str):
    return client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": key},
        json=employee_task_body(employee_id),
    )


def build_handler(tmp_path, *, development_enabled: bool = False):
    from app.services.local_employees import LocalEmployeeHandler

    agent_root = tmp_path / "agents"
    agent_root.mkdir(exist_ok=True)
    (agent_root / "engineering-frontend-developer.md").write_text("# 프론트엔드", encoding="utf-8")
    (agent_root / "product-trend-researcher.md").write_text("# 리서치", encoding="utf-8")

    class NeverCalledRunner:
        def __init__(self) -> None:
            self.called = False

        async def run(self, **kwargs):
            self.called = True
            raise AssertionError("잠금 상태에서는 Codex를 호출하면 안 됩니다.")

        async def execute(self, *args, **kwargs):
            self.called = True
            raise AssertionError("잠금 상태에서는 분리 작업공간을 열면 안 됩니다.")

    runner = NeverCalledRunner()
    handler = LocalEmployeeHandler(
        agent_root=agent_root,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        analysis_runner=runner,
        development_runner_factory=lambda location: runner,
        development_enabled=development_enabled,
    )
    return handler, runner


def task_context(employee_id: str):
    from app.worker import TaskContext

    return TaskContext(
        id="task-lock-1",
        employee_id=employee_id,
        task_type="employee_task",
        title="작업 요청",
        payload={"request": "요청 내용을 처리해줘"},
        correlation_id="correlation-lock-1",
        attempt_count=0,
    )


# --- 설정 분리 ---------------------------------------------------------------

def test_development_mode_is_locked_by_default():
    assert Settings(_env_file=None).development_mode_enabled is False


def test_default_development_scope_matches_the_independent_repository():
    settings = Settings(_env_file=None)
    repository_root = Path(__file__).resolve().parents[2]

    assert settings.development_repository_root.resolve() == repository_root
    assert settings.development_project_path == "."
    assert settings.development_allow_repository_scope is False


def test_agent_memory_root_no_longer_decides_the_development_repository(tmp_path):
    settings = Settings(
        _env_file=None,
        agent_memory_root=tmp_path / "메모리",
        development_repository_root=tmp_path / "저장소",
        development_worktree_root=tmp_path / "작업공간",
    )

    assert settings.agent_memory_root.parent != settings.development_repository_root
    assert settings.development_worktree_root != settings.development_repository_root


def test_default_worktree_root_is_outside_the_temporary_directory():
    import tempfile
    from pathlib import Path

    worktree_root = Settings(_env_file=None).development_worktree_root.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()

    assert worktree_root != temporary_root
    assert temporary_root not in worktree_root.parents


def test_worker_wires_development_runner_from_separated_settings(tmp_path):
    from app.worker import build_local_employee_handler

    settings = Settings(
        _env_file=None,
        agent_memory_root=tmp_path / "메모리",
        development_repository_root=tmp_path / "저장소",
        development_worktree_root=tmp_path / "작업공간",
    )

    from app.services.project_registry import ProjectLocation

    handler = build_local_employee_handler(settings)
    runner = handler.development_runner_factory(
        ProjectLocation(id="default", repository_root=tmp_path / "저장소", project_path="AI-company")
    )

    assert handler.development_enabled is False
    assert runner.repository_root == (tmp_path / "저장소").resolve()
    assert runner.worktree_root == (tmp_path / "작업공간" / "default").resolve()


# --- 1단계: 작업 생성 차단 ---------------------------------------------------

def test_task_api_rejects_development_request_and_queues_nothing(client):
    response = request_task(client, DEVELOPMENT_EMPLOYEE, "lock-create-1")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEVELOPMENT_MODE_PAUSED"
    assert client.get("/api/v1/tasks").json() == []


def test_task_api_still_accepts_analysis_request_while_paused(client):
    response = request_task(client, ANALYSIS_EMPLOYEE, "lock-create-2")

    assert response.status_code == 201
    assert response.json()["status"] == "queued"


def test_task_api_accepts_development_request_once_unlocked(client, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_MODE_ENABLED", "true")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        register_project(
            client,
            str(settings.development_repository_root.resolve()),
            settings.development_project_path,
        )

        response = request_task(client, DEVELOPMENT_EMPLOYEE, "lock-create-3")
        assert response.status_code == 201
        assert response.json()["status"] == "queued"
    finally:
        monkeypatch.delenv("DEVELOPMENT_MODE_ENABLED", raising=False)
        get_settings.cache_clear()


# --- 2단계: 실행 차단 ---------------------------------------------------------

async def test_handler_blocks_development_task_before_any_codex_call(tmp_path):
    from app.services.local_employees import DEVELOPMENT_MODE_PAUSED
    from app.services.task_errors import TaskHandlerError

    handler, runner = build_handler(tmp_path)

    assert handler.development_paused(DEVELOPMENT_EMPLOYEE) is True
    assert handler.configuration_error(DEVELOPMENT_EMPLOYEE) == DEVELOPMENT_MODE_PAUSED

    with pytest.raises(TaskHandlerError) as caught:
        await handler(task_context(DEVELOPMENT_EMPLOYEE))

    assert caught.value.code == "DEVELOPMENT_MODE_PAUSED"
    assert caught.value.retryable is False
    assert runner.called is False


def test_analysis_employee_is_not_paused(tmp_path):
    handler, _ = build_handler(tmp_path)

    assert handler.development_paused(ANALYSIS_EMPLOYEE) is False


def test_pause_code_is_not_reclassified_as_a_connection_failure():
    from app.services.local_employees import DEVELOPMENT_MODE_PAUSED

    assert not DEVELOPMENT_MODE_PAUSED["code"].startswith(("CODEX_", "AGENT_", "WORKSPACE_"))


# --- 3단계: 연결 상태 ---------------------------------------------------------

async def test_paused_employee_is_reported_as_stopped_and_recovers_when_unlocked():
    from app.services.local_employees import DEVELOPMENT_MODE_PAUSED
    from app.services.local_employee_connection import LocalEmployeeConnectionService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        await seed_database(session)
        await LocalEmployeeConnectionService.mark_configuration(
            session,
            DEVELOPMENT_EMPLOYEE,
            source="운영 정책",
            error=DEVELOPMENT_MODE_PAUSED,
            status_on_error="stopped",
        )
        connection = await session.get(EmployeeConnection, DEVELOPMENT_EMPLOYEE)
        assert connection.status == "stopped"
        assert connection.error["code"] == "DEVELOPMENT_MODE_PAUSED"

        await LocalEmployeeConnectionService.mark_configuration(
            session, DEVELOPMENT_EMPLOYEE, source="운영 정책", error=None
        )
        assert connection.status == "awaiting_first_sync"
        assert connection.error is None

    await engine.dispose()


def register_project(client, repository_root: str, project_path: str) -> None:
    import asyncio

    from app.models import Project
    from app.services.verification_policy import (
        DEFAULT_VERIFICATION_POLICY,
        verification_policy_hash,
    )

    async def insert() -> None:
        async with client.app.state.session_factory() as session:
            session.add(Project(
                id="default",
                name="기본 프로젝트",
                repository_root=repository_root,
                project_path=project_path,
                dependency_paths=[],
                verification_policy=dict(DEFAULT_VERIFICATION_POLICY),
                verification_policy_hash=verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
            ))
            await session.commit()

    asyncio.run(insert())


def test_development_task_is_bound_to_a_project_when_unlocked(client, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_MODE_ENABLED", "true")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        register_project(
            client,
            str(settings.development_repository_root.resolve()),
            settings.development_project_path,
        )

        development = request_task(client, DEVELOPMENT_EMPLOYEE, "bind-1")
        analysis = request_task(client, ANALYSIS_EMPLOYEE, "bind-2")

        assert development.status_code == 201
        assert analysis.status_code == 201
    finally:
        monkeypatch.delenv("DEVELOPMENT_MODE_ENABLED", raising=False)
        get_settings.cache_clear()

    import asyncio

    from app.models import QueueTask

    async def read() -> dict[str, str | None]:
        async with client.app.state.session_factory() as session:
            rows = (await session.scalars(__import__("sqlalchemy").select(QueueTask))).all()
            return {row.employee_id: row.project_id for row in rows}

    bound = asyncio.run(read())
    assert bound[DEVELOPMENT_EMPLOYEE] == "default"
    # 분석 작업은 프로젝트에 묶이지 않는다.
    assert bound[ANALYSIS_EMPLOYEE] is None


def test_development_task_is_refused_when_no_project_is_registered(client, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_MODE_ENABLED", "true")
    get_settings.cache_clear()
    try:
        response = request_task(client, DEVELOPMENT_EMPLOYEE, "bind-3")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PROJECT_NOT_REGISTERED"
        assert client.get("/api/v1/tasks").json() == []
    finally:
        monkeypatch.delenv("DEVELOPMENT_MODE_ENABLED", raising=False)
        get_settings.cache_clear()
