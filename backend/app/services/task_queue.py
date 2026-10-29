from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, false, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import QueueTask
from .event_log import EventLogService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _meeting_message(task: QueueTask, result: dict) -> str | None:
    """PM 회의 하위 작업의 실제 결과를 짧은 말풍선 문장으로 만든다."""
    if task.task_type == "project_plan":
        plan = result.get("plan")
        if isinstance(plan, dict) and plan.get("needs_user_choice") is True:
            question = plan.get("user_question")
            if isinstance(question, str) and question.strip():
                return f"확인이 필요합니다: {' '.join(question.split())}"
    if task.parent_task_id is None:
        return None
    value = result.get("response") or result.get("review")
    if task.task_type == "project_synthesis":
        report = result.get("report")
        if isinstance(report, dict):
            value = report.get("summary")
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    limit = 280
    return normalized if len(normalized) <= limit else f"{normalized[:limit - 1]}…"


class TaskQueueService:
    @staticmethod
    async def claim_next(
        session: AsyncSession,
        worker_id: str,
        supported_task_types: set[str],
        lease_seconds: float,
        now: datetime | None = None,
    ) -> QueueTask | None:
        if not supported_task_types:
            return None
        claimed_at = now or utc_now()
        candidate_id = (
            select(QueueTask.id)
            .where(
                QueueTask.status == "queued",
                QueueTask.task_type.in_(supported_task_types),
                QueueTask.attempt_count < QueueTask.max_attempts,
            )
            .order_by(QueueTask.created_at, QueueTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(QueueTask)
            .where(QueueTask.id == candidate_id, QueueTask.status == "queued")
            .values(
                status="running",
                locked_by=worker_id,
                locked_at=claimed_at,
                lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
                started_at=func.coalesce(QueueTask.started_at, claimed_at),
                attempt_count=QueueTask.attempt_count + 1,
                finished_at=None,
                updated_at=claimed_at,
            )
            .returning(QueueTask)
        )
        task = (await session.scalars(statement, execution_options={"synchronize_session": False})).one_or_none()
        if task is not None:
            await EventLogService.lock_event_sequence(session)
            EventLogService.record_task_activity(
                session,
                task,
                "task.started",
                actor_type="worker",
                detail={"attempt_count": task.attempt_count},
                created_at=claimed_at,
            )
        await session.commit()
        return task

    @staticmethod
    async def complete(
        session: AsyncSession,
        task_id: str,
        worker_id: str,
        result: dict,
        now: datetime | None = None,
        on_completed: Callable[[AsyncSession, QueueTask, dict], Awaitable[None]] | None = None,
        on_terminal: Callable[[AsyncSession, QueueTask], Awaitable[None]] | None = None,
    ) -> bool:
        finished_at = now or utc_now()
        statement = (
            update(QueueTask)
            .where(QueueTask.id == task_id, QueueTask.status == "running", QueueTask.locked_by == worker_id)
            .values(
                status="completed",
                result=result,
                error=None,
                locked_by=None,
                locked_at=None,
                lease_expires_at=None,
                finished_at=finished_at,
                updated_at=finished_at,
            )
            .returning(QueueTask)
        )
        task = (await session.scalars(statement, execution_options={"synchronize_session": False})).one_or_none()
        if task is not None:
            await EventLogService.lock_event_sequence(session)
            EventLogService.record_task_activity(
                session,
                task,
                "task.completed",
                actor_type="worker",
                detail={"meeting_message": message} if (message := _meeting_message(task, result)) else None,
                created_at=finished_at,
            )
            # 같은 트랜잭션 안에서 처리한다. 작업만 완료되고 승인이 없는 상태가 생기면
            # 사용자는 결과를 보고도 반영할 수 없고 워커는 다시 만들지 않는다.
            if on_completed is not None:
                await on_completed(session, task, result)
            if on_terminal is not None:
                await on_terminal(session, task)
        await session.commit()
        return task is not None

    @staticmethod
    async def heartbeat(
        session: AsyncSession,
        task_id: str,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> str | None:
        heartbeat_at = now or utc_now()
        statement = (
            update(QueueTask)
            .where(QueueTask.id == task_id, QueueTask.status == "running", QueueTask.locked_by == worker_id)
            .values(
                lease_expires_at=heartbeat_at + timedelta(seconds=lease_seconds),
                updated_at=heartbeat_at,
            )
            .returning(QueueTask.status)
        )
        status = await session.scalar(statement, execution_options={"synchronize_session": False})
        if status is None:
            status = await session.scalar(
                select(QueueTask.status).where(QueueTask.id == task_id, QueueTask.locked_by == worker_id)
            )
        await session.commit()
        return status

    @staticmethod
    async def mark_cancelled(
        session: AsyncSession,
        task_id: str,
        worker_id: str,
        now: datetime | None = None,
        on_terminal: Callable[[AsyncSession, QueueTask], Awaitable[None]] | None = None,
    ) -> bool:
        finished_at = now or utc_now()
        statement = (
            update(QueueTask)
            .where(
                QueueTask.id == task_id,
                QueueTask.status == "cancel_requested",
                QueueTask.locked_by == worker_id,
            )
            .values(
                status="cancelled",
                locked_by=None,
                locked_at=None,
                lease_expires_at=None,
                finished_at=finished_at,
                updated_at=finished_at,
            )
            .returning(QueueTask)
        )
        task = (await session.scalars(statement, execution_options={"synchronize_session": False})).one_or_none()
        if task is not None:
            await EventLogService.lock_event_sequence(session)
            EventLogService.record_task_activity(
                session, task, "task.cancelled", actor_type="worker", created_at=finished_at
            )
            if on_terminal is not None:
                await on_terminal(session, task)
        await session.commit()
        return task is not None

    @staticmethod
    async def fail(
        session: AsyncSession,
        task_id: str,
        worker_id: str,
        error: dict,
        retryable: bool = True,
        now: datetime | None = None,
        on_terminal: Callable[[AsyncSession, QueueTask], Awaitable[None]] | None = None,
    ) -> QueueTask | None:
        failed_at = now or utc_now()
        can_retry = QueueTask.attempt_count < QueueTask.max_attempts if retryable else false()
        statement = (
            update(QueueTask)
            .where(QueueTask.id == task_id, QueueTask.status == "running", QueueTask.locked_by == worker_id)
            .values(
                status=case((can_retry, "queued"), else_="failed"),
                error=error,
                locked_by=None,
                locked_at=None,
                lease_expires_at=None,
                finished_at=case((can_retry, None), else_=failed_at),
                updated_at=failed_at,
            )
            .returning(QueueTask)
        )
        task = (await session.scalars(statement, execution_options={"synchronize_session": False})).one_or_none()
        if task is not None:
            await EventLogService.lock_event_sequence(session)
            retrying = task.status == "queued"
            EventLogService.record_task_activity(
                session,
                task,
                "task.retry_scheduled" if retrying else "task.failed",
                actor_type="worker",
                detail={"attempt_count": task.attempt_count, "max_attempts": task.max_attempts},
                created_at=failed_at,
            )
            EventLogService.record_task_alert(
                session,
                task,
                severity="warning" if retrying else "error",
                code="TASK_RETRY_SCHEDULED" if retrying else "TASK_FAILED",
                message="작업 실행에 실패하여 다시 시도합니다" if retrying else "작업 실행에 최종 실패했습니다",
                detail={"attempt_count": task.attempt_count, "max_attempts": task.max_attempts},
                created_at=failed_at,
            )
            if task.status == "failed" and on_terminal is not None:
                await on_terminal(session, task)
        await session.commit()
        return task

    @staticmethod
    async def recover_expired(session: AsyncSession, now: datetime | None = None) -> int:
        recovered_at = now or utc_now()
        tasks = list(
            (
                await session.scalars(
                    select(QueueTask)
                    .where(
                        QueueTask.status.in_(("running", "cancel_requested")),
                        QueueTask.lease_expires_at.is_not(None),
                        QueueTask.lease_expires_at <= recovered_at,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        if tasks:
            await EventLogService.lock_event_sequence(session)
        for task in tasks:
            was_cancel_requested = task.status == "cancel_requested"
            can_retry = task.attempt_count < task.max_attempts
            task.status = "cancelled" if was_cancel_requested else ("queued" if can_retry else "failed")
            task.error = None if was_cancel_requested else {
                "code": "WORKER_LEASE_EXPIRED",
                "message": "작업자 연결이 끊겨 작업을 복구했습니다.",
            }
            task.locked_by = None
            task.locked_at = None
            task.lease_expires_at = None
            task.finished_at = recovered_at if task.status in ("cancelled", "failed") else None
            task.updated_at = recovered_at
            EventLogService.record_task_activity(
                session,
                task,
                "task.cancelled" if was_cancel_requested else "task.lease_expired",
                actor_type="system",
                detail={"next_status": task.status},
                created_at=recovered_at,
            )
            if not was_cancel_requested:
                EventLogService.record_task_alert(
                    session,
                    task,
                    severity="warning" if can_retry else "error",
                    code="WORKER_LEASE_EXPIRED",
                    message="작업자 연결이 끊겨 작업을 복구했습니다",
                    detail={"next_status": task.status},
                    created_at=recovered_at,
                )
        await session.commit()
        return len(tasks)
