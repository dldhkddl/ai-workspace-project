import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Employee, QueueTask, RealtimeEvent, Team
from app.schemas import TaskCreate
from app.services.task_queue import TaskQueueService
from app.services.tasks import (
    IdempotencyConflictError,
    TaskNotCancellableError,
    TaskService,
    task_request_hash,
)
from app.services.event_log import EventLogService


pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="로컬 PostgreSQL 동시성 테스트")

DATABASE_URL = os.getenv(
    "POSTGRES_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/ai_company_test?ssl=disable",
)
if make_url(DATABASE_URL).database == "postgres":
    raise RuntimeError("PostgreSQL 동시성 테스트에 운영 DB 'postgres'를 사용할 수 없습니다.")
TEST_RUN_ID = str(uuid4())
TEST_TEAM_ID = f"task-test-team-{TEST_RUN_ID}"
TEST_EMPLOYEE_ID = "general-manager"


def request(title: str = "동시 작업") -> TaskCreate:
    return TaskCreate(
        employee_id=TEST_EMPLOYEE_ID,
        task_type="general_manager_review",
        title=title,
        payload={"source": "postgres-concurrency-test"},
    )


async def prepare_test_employee(factory):
    async with factory() as session:
        session.add(Team(id=TEST_TEAM_ID, name="작업 큐 테스트", short_name="큐 테스트", color="#000000"))
        session.add(
            Employee(
                id=TEST_EMPLOYEE_ID,
                team_id=TEST_TEAM_ID,
                name="작업 큐 테스트 직원",
                role="동시성 검증",
                icon="🧪",
                allowed_tools=[],
                approval_required=False,
                planned_source="테스트",
                planned_features=[],
            )
        )
        await session.commit()


async def cleanup_test_employee(factory):
    async with factory() as session:
        await session.execute(delete(QueueTask).where(QueueTask.employee_id == TEST_EMPLOYEE_ID))
        await session.execute(delete(Employee).where(Employee.id == TEST_EMPLOYEE_ID))
        await session.execute(delete(Team).where(Team.id == TEST_TEAM_ID))
        await session.commit()


async def wait_until_unique_key_lock(factory, backend_pid: int, pending: asyncio.Task):
    for _ in range(100):
        assert not pending.done(), "경합 요청이 PostgreSQL 잠금을 기다리기 전에 종료되었습니다."
        async with factory() as observer:
            wait_event_type = await observer.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": backend_pid},
            )
        if wait_event_type == "Lock":
            return
        await asyncio.sleep(0.01)
    pytest.fail("경합 요청의 PostgreSQL 고유 키 잠금 대기를 확인하지 못했습니다.")


async def run_locked_idempotency_race(factory, stored_request: TaskCreate, contender_request: TaskCreate, key: str):
    async with factory() as blocker, factory() as contender:
        stored = QueueTask(
            employee_id=stored_request.employee_id,
            task_type=stored_request.task_type,
            title=stored_request.title,
            input_payload=stored_request.payload,
            idempotency_key=key,
            request_hash=task_request_hash(stored_request),
        )
        blocker.add(stored)
        await blocker.flush()

        backend_pid = await contender.scalar(text("SELECT pg_backend_pid()"))
        pending = asyncio.create_task(TaskService.create(contender, contender_request, key))
        await wait_until_unique_key_lock(factory, backend_pid, pending)
        await blocker.commit()
        try:
            return await asyncio.wait_for(pending, timeout=2)
        except Exception:
            if not pending.done():
                pending.cancel()
            raise


@pytest.mark.asyncio
async def test_concurrent_same_request_returns_one_task_and_different_request_conflicts():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    same_key = f"concurrent-same-{uuid4()}"
    conflict_key = f"concurrent-conflict-{uuid4()}"
    try:
        await prepare_test_employee(factory)
        same_result = await run_locked_idempotency_race(factory, request(), request(), same_key)
        assert same_result.created is False
        async with factory() as session:
            assert await session.scalar(
                select(func.count()).select_from(QueueTask).where(QueueTask.idempotency_key == same_key)
            ) == 1

        with pytest.raises(IdempotencyConflictError):
            await run_locked_idempotency_race(factory, request("첫 요청"), request("다른 요청"), conflict_key)
    finally:
        await cleanup_test_employee(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_transition_is_atomic_and_running_requests_cancellation():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    key = f"concurrent-cancel-{uuid4()}"
    try:
        await prepare_test_employee(factory)
        async with factory() as session:
            task = (await TaskService.create(session, request("취소 경쟁"), key)).task
            task_id = task.id
            await session.execute(update(QueueTask).where(QueueTask.id == task_id).values(status="running"))
            await session.commit()

        async with factory() as first, factory() as second:
            results = await asyncio.gather(
                TaskService.cancel(first, task_id),
                TaskService.cancel(second, task_id),
                return_exceptions=True,
            )
        successful = [item for item in results if isinstance(item, QueueTask)]
        assert len(successful) == 1
        assert successful[0].status == "cancel_requested"
        assert sum(isinstance(item, TaskNotCancellableError) for item in results) == 1
    finally:
        await cleanup_test_employee(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_postgres_workers_cannot_claim_the_same_task():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    key = f"worker-claim-{uuid4()}"
    try:
        await prepare_test_employee(factory)
        async with factory() as session:
            task = (await TaskService.create(session, request("원자적 선점"), key)).task

        async with factory() as first, factory() as second:
            claims = await asyncio.gather(
                TaskQueueService.claim_next(first, "worker-1", {"general_manager_review"}, 60),
                TaskQueueService.claim_next(second, "worker-2", {"general_manager_review"}, 60),
            )

        claimed = [item for item in claims if item is not None]
        assert len(claimed) == 1
        assert claimed[0].id == task.id
        assert claimed[0].attempt_count == 1
    finally:
        await cleanup_test_employee(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_expired_lease_is_recovered_after_restart():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    key = f"worker-recovery-{uuid4()}"
    now = datetime.now(timezone.utc)
    try:
        await prepare_test_employee(factory)
        async with factory() as session:
            task = (await TaskService.create(session, request("재시작 복구"), key)).task
        async with factory() as session:
            await TaskQueueService.claim_next(
                session,
                "stopped-worker",
                {"general_manager_review"},
                30,
                now=now - timedelta(minutes=1),
            )
        async with factory() as session:
            assert await TaskQueueService.recover_expired(session, now=now) == 1
        async with factory() as session:
            recovered = await session.get(QueueTask, task.id)
            assert recovered.status == "queued"
            assert recovered.locked_by is None
            assert recovered.error["code"] == "WORKER_LEASE_EXPIRED"
    finally:
        await cleanup_test_employee(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_realtime_event_ids_follow_commit_order():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    resource_prefix = f"event-order-{uuid4()}"

    async def insert_second() -> int:
        async with factory() as session:
            await EventLogService.lock_event_sequence(session)
            event = RealtimeEvent(
                event_type="test.created",
                resource_type="test",
                resource_id=f"{resource_prefix}-second",
                payload={},
            )
            session.add(event)
            await session.commit()
            return event.id

    try:
        async with factory() as first:
            await EventLogService.lock_event_sequence(first)
            first_event = RealtimeEvent(
                event_type="test.created",
                resource_type="test",
                resource_id=f"{resource_prefix}-first",
                payload={},
            )
            first.add(first_event)
            await first.flush()
            pending = asyncio.create_task(insert_second())
            await asyncio.sleep(0.05)
            assert not pending.done()
            await first.commit()
            second_id = await asyncio.wait_for(pending, timeout=2)
            assert first_event.id < second_id
    finally:
        async with factory() as session:
            await session.execute(delete(RealtimeEvent).where(RealtimeEvent.resource_id.like(f"{resource_prefix}%")))
            await session.commit()
        await engine.dispose()
