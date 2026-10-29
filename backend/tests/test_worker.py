import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, QueueTask
from app.schemas import TaskCreate
from app.seed import seed_database
from app.services.tasks import TaskService, task_request_hash
from app.worker import TaskContext, TaskWorker
from app.services.task_errors import TaskHandlerError


@pytest.fixture()
async def worker_factory(tmp_path: Path):
    database_path = tmp_path / "worker.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_database(session)
    yield factory
    await engine.dispose()
    database_path.unlink(missing_ok=True)


async def create_task(factory, key: str, task_type: str = "verification") -> QueueTask:
    request = TaskCreate(
        employee_id="smartstore-operator",
        task_type=task_type,
        title="워커 실행 검증",
        payload={"value": 21},
    )
    async with factory() as session:
        task = QueueTask(
            employee_id=request.employee_id,
            task_type=request.task_type,
            title=request.title,
            input_payload=request.payload,
            idempotency_key=key,
            request_hash=task_request_hash(request),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def get_task(factory, task_id: str) -> QueueTask:
    async with factory() as session:
        task = await session.get(QueueTask, task_id)
        assert task is not None
        return task


def test_openai_role_settings_default_to_quality_first_models():
    from app.config import Settings

    settings = Settings(_env_file=None)

    assert settings.manager_ai_runtime == "codex"
    assert settings.openai_model == "gpt-5.4-mini"
    assert settings.openai_general_manager_model == "gpt-5.6-sol"
    assert settings.openai_general_manager_reasoning_effort == "high"
    assert settings.openai_project_manager_model == "gpt-5.6-sol"
    assert settings.openai_project_manager_reasoning_effort == "medium"


def test_role_handlers_use_codex_locally_without_api_key(tmp_path):
    from app.config import Settings
    from app.services.codex_managers import CodexGeneralManagerHandler, CodexProjectManagerHandler
    from app.worker import build_role_handlers

    settings = Settings(_env_file=None, manager_ai_runtime="codex", agent_memory_root=tmp_path)

    general_manager, project_manager = build_role_handlers(settings)

    assert isinstance(general_manager, CodexGeneralManagerHandler)
    assert isinstance(project_manager, CodexProjectManagerHandler)
    assert general_manager.source == "Codex SDK · ChatGPT"
    assert project_manager.source == "Codex SDK · ChatGPT · agents-orchestrator"


def test_role_handlers_use_openai_only_when_explicitly_selected(tmp_path):
    from app.config import Settings
    from app.services.openai_general_manager import OpenAIGeneralManagerHandler
    from app.services.openai_project_manager import OpenAIProjectManagerHandler
    from app.worker import build_role_handlers

    settings = Settings(
        _env_file=None,
        manager_ai_runtime="openai",
        openai_api_key="test-key",
        agent_memory_root=tmp_path,
    )

    general_manager, project_manager = build_role_handlers(settings)

    assert isinstance(general_manager, OpenAIGeneralManagerHandler)
    assert isinstance(project_manager, OpenAIProjectManagerHandler)


def test_openai_role_handlers_use_quality_first_models(tmp_path):
    from app.config import Settings
    from app.worker import build_openai_role_handlers

    settings = Settings(
        _env_file=None,
        openai_api_key="test-key",
        agent_memory_root=tmp_path,
        openai_general_manager_model="general-manager-model",
        openai_general_manager_reasoning_effort="xhigh",
        openai_project_manager_model="project-manager-model",
        openai_project_manager_reasoning_effort="low",
    )

    general_manager, project_manager = build_openai_role_handlers(settings)

    assert general_manager.model == "general-manager-model"
    assert general_manager.reasoning_effort == "xhigh"
    assert project_manager.model == "project-manager-model"
    assert project_manager.reasoning_effort == "low"


@pytest.mark.asyncio
async def test_worker_completes_task_with_handler_result(worker_factory):
    task = await create_task(worker_factory, "worker-success")

    async def handler(context: TaskContext) -> dict:
        return {"answer": context.payload["value"] * 2}

    worker = TaskWorker(worker_factory, "worker-1", {"verification": handler})

    assert await worker.run_once() is True
    stored = await get_task(worker_factory, task.id)
    assert stored.status == "completed"
    assert stored.result == {"answer": 42}


@pytest.mark.asyncio
async def test_worker_leaves_unsupported_task_queued(worker_factory):
    task = await create_task(worker_factory, "worker-unsupported", "unknown")
    worker = TaskWorker(worker_factory, "worker-1", {})

    assert await worker.run_once() is False
    assert (await get_task(worker_factory, task.id)).status == "queued"


@pytest.mark.asyncio
async def test_worker_retries_failure_then_marks_task_failed(worker_factory):
    task = await create_task(worker_factory, "worker-failure")

    async def failing_handler(_context: TaskContext) -> dict:
        raise RuntimeError("민감한 내부 오류")

    worker = TaskWorker(worker_factory, "worker-1", {"verification": failing_handler})

    for expected_status in ("queued", "queued", "failed"):
        assert await worker.run_once() is True
        stored = await get_task(worker_factory, task.id)
        assert stored.status == expected_status
        assert stored.error["code"] == "TASK_EXECUTION_FAILED"
        assert "민감한 내부 오류" not in stored.error["message"]


@pytest.mark.asyncio
async def test_worker_does_not_retry_permanent_handler_failure(worker_factory):
    task = await create_task(worker_factory, "worker-permanent-failure")

    async def failing_handler(_context: TaskContext) -> dict:
        raise TaskHandlerError("OPENAI_AUTH_FAILED", "OpenAI API 인증에 실패했습니다.", retryable=False)

    worker = TaskWorker(worker_factory, "worker-1", {"verification": failing_handler})

    assert await worker.run_once() is True
    stored = await get_task(worker_factory, task.id)
    assert stored.status == "failed"
    assert stored.attempt_count == 1
    assert stored.error == {
        "code": "OPENAI_AUTH_FAILED",
        "message": "OpenAI API 인증에 실패했습니다.",
    }


@pytest.mark.asyncio
async def test_connection_status_failure_does_not_replace_successful_handler_result(worker_factory, monkeypatch):
    from app.services.general_manager_connection import GeneralManagerConnectionService
    from app.worker import build_general_manager_handler

    async def handler(_context: TaskContext) -> dict:
        return {"review": "완료"}

    async def fail_status_update(_session):
        raise RuntimeError("connection database unavailable")

    monkeypatch.setattr(GeneralManagerConnectionService, "mark_success", fail_status_update)
    wrapped = build_general_manager_handler(handler, worker_factory)

    assert await wrapped(TaskContext(
        id="task-1",
        employee_id="general-manager",
        task_type="general_manager_review",
        title="검토",
        payload={},
        correlation_id="correlation-1",
        attempt_count=1,
    )) == {"review": "완료"}


@pytest.mark.asyncio
async def test_connection_status_failure_preserves_permanent_handler_error(worker_factory, monkeypatch):
    from app.services.general_manager_connection import GeneralManagerConnectionService
    from app.worker import build_general_manager_handler

    original = TaskHandlerError("OPENAI_AUTH_FAILED", "인증 실패", retryable=False)

    async def handler(_context: TaskContext) -> dict:
        raise original

    async def fail_status_update(_session, **_kwargs):
        raise RuntimeError("connection database unavailable")

    monkeypatch.setattr(GeneralManagerConnectionService, "mark_failure", fail_status_update)
    wrapped = build_general_manager_handler(handler, worker_factory)

    with pytest.raises(TaskHandlerError) as caught:
        await wrapped(TaskContext(
            id="task-1",
            employee_id="general-manager",
            task_type="general_manager_review",
            title="검토",
            payload={},
            correlation_id="correlation-1",
            attempt_count=1,
        ))

    assert caught.value is original
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_project_manager_handler_marks_connection_healthy(worker_factory):
    from app.models import EmployeeConnection
    from app.worker import build_project_manager_handler

    async def handler(_context: TaskContext) -> dict:
        return {"plan": {"objective": "완료"}}

    wrapped = build_project_manager_handler(handler, worker_factory)
    result = await wrapped(TaskContext(
        id="task-pm-1",
        employee_id="project-manager",
        task_type="project_plan",
        title="계획",
        payload={},
        correlation_id="correlation-pm-1",
        attempt_count=1,
    ))

    async with worker_factory() as session:
        connection = await session.get(EmployeeConnection, "project-manager")

    assert result == {"plan": {"objective": "완료"}}
    assert connection.status == "healthy"


@pytest.mark.asyncio
async def test_project_manager_handler_records_missing_agent_configuration(worker_factory):
    from app.models import EmployeeConnection
    from app.worker import build_project_manager_handler

    async def handler(_context: TaskContext) -> dict:
        raise TaskHandlerError(
            "AGENT_INSTRUCTIONS_NOT_FOUND",
            "프로젝트 매니저 에이전트 파일을 찾을 수 없습니다.",
            retryable=False,
        )

    wrapped = build_project_manager_handler(handler, worker_factory)
    with pytest.raises(TaskHandlerError):
        await wrapped(TaskContext(
            id="task-pm-1",
            employee_id="project-manager",
            task_type="project_plan",
            title="계획",
            payload={},
            correlation_id="correlation-pm-1",
            attempt_count=1,
        ))

    async with worker_factory() as session:
        connection = await session.get(EmployeeConnection, "project-manager")

    assert connection.status == "setup_required"
    assert connection.error["code"] == "AGENT_INSTRUCTIONS_NOT_FOUND"


@pytest.mark.asyncio
async def test_worker_timeout_uses_retry_policy(worker_factory):
    task = await create_task(worker_factory, "worker-timeout")

    async def slow_handler(_context: TaskContext) -> dict:
        await asyncio.Event().wait()
        return {}

    worker = TaskWorker(
        worker_factory,
        "worker-1",
        {"verification": slow_handler},
        lease_seconds=0.1,
        heartbeat_seconds=0.01,
        task_timeout_seconds=0.03,
    )

    assert await worker.run_once() is True
    stored = await get_task(worker_factory, task.id)
    assert stored.status == "queued"
    assert stored.error["code"] == "TASK_TIMEOUT"


@pytest.mark.asyncio
async def test_worker_observes_cancel_request_and_cancels_handler(worker_factory):
    task = await create_task(worker_factory, "worker-cancel")
    started = asyncio.Event()
    handler_cancelled = asyncio.Event()

    async def waiting_handler(_context: TaskContext) -> dict:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            handler_cancelled.set()
            raise

    worker = TaskWorker(
        worker_factory,
        "worker-1",
        {"verification": waiting_handler},
        lease_seconds=0.2,
        heartbeat_seconds=0.01,
        task_timeout_seconds=1,
    )

    running = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), timeout=1)
    async with worker_factory() as session:
        cancelled = await TaskService.cancel(session, task.id)
        assert cancelled.status == "cancel_requested"

    assert await asyncio.wait_for(running, timeout=1) is True
    assert handler_cancelled.is_set()
    stored = await get_task(worker_factory, task.id)
    assert stored.status == "cancelled"
    assert stored.finished_at is not None


@pytest.mark.asyncio
async def test_worker_loop_stops_without_claiming_unsupported_tasks(worker_factory):
    task = await create_task(worker_factory, "worker-loop-unsupported", "unknown")
    worker = TaskWorker(worker_factory, "worker-1", {})
    stop_event = asyncio.Event()

    running = asyncio.create_task(worker.run_forever(stop_event=stop_event, poll_seconds=0.01))
    await asyncio.sleep(0.02)
    stop_event.set()
    await asyncio.wait_for(running, timeout=1)

    assert (await get_task(worker_factory, task.id)).status == "queued"


@pytest.mark.asyncio
async def test_worker_retries_result_that_cannot_be_stored_as_json(worker_factory):
    task = await create_task(worker_factory, "worker-invalid-result")

    async def invalid_result_handler(_context: TaskContext) -> dict:
        return {"value": float("nan")}

    worker = TaskWorker(worker_factory, "worker-1", {"verification": invalid_result_handler})

    assert await worker.run_once() is True
    stored = await get_task(worker_factory, task.id)
    assert stored.status == "queued"
    assert stored.error["code"] == "TASK_EXECUTION_FAILED"


@pytest.mark.asyncio
async def test_worker_calls_terminal_hook_after_final_failure(worker_factory):
    task = await create_task(worker_factory, "worker-terminal-hook")
    observed = []

    async def failing_handler(_context: TaskContext) -> dict:
        from app.services.task_errors import TaskHandlerError
        raise TaskHandlerError("FINAL_FAILURE", "최종 실패", retryable=False)

    async def terminal_hook(_session, stored):
        observed.append((stored.id, stored.status))

    worker = TaskWorker(
        worker_factory, "worker-1", {"verification": failing_handler},
        on_terminal=terminal_hook,
    )

    assert await worker.run_once() is True
    assert observed == [(task.id, "failed")]


@pytest.mark.asyncio
async def test_worker_finishes_cancellation_when_handler_cleanup_raises(worker_factory):
    task = await create_task(worker_factory, "worker-cancel-cleanup-error")
    started = asyncio.Event()

    async def cleanup_error_handler(_context: TaskContext) -> dict:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("취소 정리 실패") from exc

    worker = TaskWorker(
        worker_factory,
        "worker-1",
        {"verification": cleanup_error_handler},
        lease_seconds=0.2,
        heartbeat_seconds=0.01,
        task_timeout_seconds=1,
    )

    running = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), timeout=1)
    async with worker_factory() as session:
        await TaskService.cancel(session, task.id)

    assert await asyncio.wait_for(running, timeout=1) is True
    assert (await get_task(worker_factory, task.id)).status == "cancelled"


async def test_a_failing_completion_hook_fails_the_task_but_keeps_the_worker_running():
    """완료 훅이 터져도 워커 프로세스는 계속 돌아야 한다.

    결과 형식 오류는 작업 최종 실패, 일시적 오류는 재시도로 갈린다.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, QueueTask
    from app.seed import seed_database
    from app.services.task_errors import TaskHandlerError
    from app.worker import TaskContext, TaskWorker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed_database(session)
        for index, (task_id, key) in enumerate((("hook-fatal", "k1"), ("hook-temporary", "k2"))):
            session.add(QueueTask(
                id=task_id, employee_id="frontend", task_type="employee_task",
                title=f"작업 {index}", input_payload={}, idempotency_key=key,
                request_hash="hash",
            ))
        await session.commit()

    async def handler(context: TaskContext) -> dict:
        return {"kind": "changes"}

    async def hook(session, task, result):
        if task.id == "hook-fatal":
            raise TaskHandlerError("APPROVAL_RESULT_INCOMPLETE", "형식 오류", retryable=False)
        raise RuntimeError("일시적인 DB 오류")

    worker = TaskWorker(
        factory, worker_id="worker-hook", handlers={"employee_task": handler},
        lease_seconds=30, heartbeat_seconds=5, task_timeout_seconds=10, on_completed=hook,
    )

    assert await worker.run_once() is True
    assert await worker.run_once() is True

    async with factory() as session:
        rows = {row.id: row for row in (await session.scalars(sa.select(QueueTask))).all()}
    # 형식 오류는 다시 해도 같으므로 최종 실패
    assert rows["hook-fatal"].status == "failed"
    assert rows["hook-fatal"].error["code"] == "APPROVAL_RESULT_INCOMPLETE"
    # 일시적 오류는 다시 시도
    assert rows["hook-temporary"].status == "queued"
    assert rows["hook-temporary"].error["code"] == "TASK_COMPLETION_FAILED"
    # 어느 쪽도 completed 로 남지 않는다
    assert all(row.status != "completed" for row in rows.values())

    await engine.dispose()
