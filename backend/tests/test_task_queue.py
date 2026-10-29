from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Activity, Alert, AuditLog, Base, QueueTask, RealtimeEvent
from app.schemas import TaskCreate
from app.seed import seed_database
from app.services.task_queue import TaskQueueService
from app.services.tasks import TaskService, task_request_hash


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@pytest.fixture()
async def queue_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_database(session)
    yield factory
    await engine.dispose()


async def create_task(
    factory, key: str, task_type: str = "verification", employee_id: str = "smartstore-operator"
) -> QueueTask:
    request = TaskCreate(
        employee_id=employee_id,
        task_type=task_type,
        title="워커 검증 작업",
        payload={"source": "queue-test"},
    )
    async with factory() as session:
        if task_type == "employee_task":
            return (await TaskService.create(session, request, key)).task
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


@pytest.mark.asyncio
async def test_claim_only_supported_task_and_records_lease(queue_factory):
    supported = await create_task(queue_factory, "supported-task")
    unsupported = await create_task(queue_factory, "unsupported-task", "unknown")
    now = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)

    async with queue_factory() as session:
        claimed = await TaskQueueService.claim_next(
            session,
            worker_id="worker-1",
            supported_task_types={"verification"},
            lease_seconds=60,
            now=now,
        )

    assert claimed is not None
    assert claimed.id == supported.id
    assert claimed.status == "running"
    assert claimed.locked_by == "worker-1"
    assert claimed.attempt_count == 1
    assert as_utc(claimed.started_at) == now
    assert as_utc(claimed.lease_expires_at) == now + timedelta(seconds=60)
    async with queue_factory() as session:
        assert (await session.get(QueueTask, unsupported.id)).status == "queued"


@pytest.mark.asyncio
async def test_only_lease_owner_can_complete_task(queue_factory):
    task = await create_task(queue_factory, "complete-task")
    async with queue_factory() as session:
        claimed = await TaskQueueService.claim_next(session, "worker-1", {"verification"}, 60)
        assert claimed is not None

    async with queue_factory() as session:
        assert await TaskQueueService.complete(session, task.id, "other-worker", {"ok": True}) is False
    async with queue_factory() as session:
        assert await TaskQueueService.complete(session, task.id, "worker-1", {"ok": True}) is True
        completed = await session.get(QueueTask, task.id)
        assert completed.status == "completed"
        assert completed.result == {"ok": True}
        assert completed.finished_at is not None
        assert completed.locked_by is None


@pytest.mark.asyncio
async def test_task_activity_contains_context_for_office_meeting_bubbles(queue_factory):
    task = await create_task(
        queue_factory, "meeting-activity", "employee_task", "research-factcheck"
    )

    async with queue_factory() as session:
        activity = await session.scalar(select(Activity).where(
            Activity.task_id == task.id,
            Activity.event_type == "task.queued",
        ))

    assert activity is not None
    assert activity.detail == {
        "task_title": "워커 검증 작업",
        "task_type": "employee_task",
        "parent_task_id": None,
    }


@pytest.mark.asyncio
async def test_completed_pm_assignment_activity_contains_actual_result_summary(queue_factory):
    parent = await create_task(
        queue_factory, "meeting-parent", "project_plan", "project-manager"
    )
    task = await create_task(
        queue_factory, "meeting-child", "employee_task", "research-factcheck"
    )
    async with queue_factory() as session:
        stored = await session.get(QueueTask, task.id)
        stored.parent_task_id = parent.id
        await session.commit()
    async with queue_factory() as session:
        await TaskQueueService.claim_next(session, "worker-1", {"employee_task"}, 60)
    async with queue_factory() as session:
        assert await TaskQueueService.complete(
            session,
            task.id,
            "worker-1",
            {"response": "20대 고객은 짧은 영상에서 구매 전환율이 높았습니다."},
        ) is True
    async with queue_factory() as session:
        activity = await session.scalar(select(Activity).where(
            Activity.task_id == task.id,
            Activity.event_type == "task.completed",
        ))

    assert activity is not None
    assert activity.detail["meeting_message"] == (
        "20대 고객은 짧은 영상에서 구매 전환율이 높았습니다."
    )


@pytest.mark.asyncio
async def test_completed_pm_plan_activity_says_user_response_is_required(queue_factory):
    task = await create_task(
        queue_factory, "pm-question", "project_plan", "project-manager"
    )
    async with queue_factory() as session:
        await TaskQueueService.claim_next(session, "worker-1", {"project_plan"}, 60)
    async with queue_factory() as session:
        assert await TaskQueueService.complete(session, task.id, "worker-1", {
            "plan": {
                "needs_user_choice": True,
                "user_question": "보관함 정리와 자동화 중 무엇을 먼저 할까요?",
            }
        }) is True
    async with queue_factory() as session:
        activity = await session.scalar(select(Activity).where(
            Activity.task_id == task.id,
            Activity.event_type == "task.completed",
        ))

    assert activity is not None
    assert activity.detail["meeting_message"] == (
        "확인이 필요합니다: 보관함 정리와 자동화 중 무엇을 먼저 할까요?"
    )


@pytest.mark.asyncio
async def test_failure_requeues_until_attempt_limit_then_fails(queue_factory):
    task = await create_task(queue_factory, "retry-task")

    for expected_status in ("queued", "queued", "failed"):
        async with queue_factory() as session:
            claimed = await TaskQueueService.claim_next(session, "worker-1", {"verification"}, 60)
            assert claimed is not None
        async with queue_factory() as session:
            failed = await TaskQueueService.fail(
                session,
                task.id,
                "worker-1",
                {"code": "TEST_FAILURE", "message": "검증 실패"},
            )
            assert failed is not None
            assert failed.status == expected_status

    assert failed.attempt_count == 3
    assert failed.finished_at is not None
    async with queue_factory() as session:
        activities = list((await session.scalars(
            select(Activity).where(Activity.task_id == task.id).order_by(Activity.created_at)
        )).all())
        alerts = list((await session.scalars(
            select(Alert).where(Alert.task_id == task.id).order_by(Alert.created_at)
        )).all())
        events = list((await session.scalars(select(RealtimeEvent).order_by(RealtimeEvent.id))).all())
        audits = list((await session.scalars(
            select(AuditLog).where(AuditLog.resource_id == task.id).order_by(AuditLog.created_at)
        )).all())
    failure_events = [item.event_type for item in activities if item.event_type != "task.started"]
    assert failure_events[-3:] == [
        "task.retry_scheduled", "task.retry_scheduled", "task.failed",
    ]
    assert [item.severity for item in alerts] == ["warning", "warning", "error"]
    assert [item.id for item in events] == sorted(item.id for item in events)
    assert audits[-1].action == "task.failed"


@pytest.mark.asyncio
async def test_recover_expired_tasks_requeues_running_and_finishes_cancel_request(queue_factory):
    retry_task = await create_task(queue_factory, "expired-running")
    cancel_task = await create_task(queue_factory, "expired-cancel")
    now = datetime(2026, 8, 21, 7, 10, tzinfo=timezone.utc)

    for task in (retry_task, cancel_task):
        async with queue_factory() as session:
            claimed = await TaskQueueService.claim_next(
                session, f"worker-{task.id}", {"verification"}, 60, now=now - timedelta(minutes=2)
            )
            assert claimed is not None
    async with queue_factory() as session:
        stored = await session.get(QueueTask, cancel_task.id)
        stored.status = "cancel_requested"
        await session.commit()

    async with queue_factory() as session:
        recovered = await TaskQueueService.recover_expired(session, now=now)
        assert recovered == 2

    async with queue_factory() as session:
        retry_stored = await session.get(QueueTask, retry_task.id)
        cancel_stored = await session.get(QueueTask, cancel_task.id)
        assert retry_stored.status == "queued"
        assert retry_stored.locked_by is None
        assert retry_stored.error["code"] == "WORKER_LEASE_EXPIRED"
        assert cancel_stored.status == "cancelled"
        assert as_utc(cancel_stored.finished_at) == now
