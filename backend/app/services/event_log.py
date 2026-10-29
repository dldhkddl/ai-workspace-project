from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Activity, Alert, AuditLog, QueueTask, RealtimeEvent, utc_now


TASK_ACTIVITY_TITLES = {
    "task.queued": "작업이 대기열에 등록되었습니다",
    "task.started": "작업 실행을 시작했습니다",
    "task.retry_scheduled": "작업을 다시 시도할 예정입니다",
    "task.completed": "작업을 완료했습니다",
    "task.failed": "작업 실행에 실패했습니다",
    "task.cancel_requested": "작업 취소를 요청했습니다",
    "task.cancelled": "작업을 취소했습니다",
    "task.lease_expired": "작업 연결이 만료되어 복구했습니다",
}


def _activity_payload(activity: Activity) -> dict:
    return {
        "id": activity.id,
        "employee_id": activity.employee_id,
        "task_id": activity.task_id,
        "event_type": activity.event_type,
        "title": activity.title,
        "detail": activity.detail,
        "correlation_id": activity.correlation_id,
        "created_at": activity.created_at.isoformat(),
    }


def _alert_payload(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "employee_id": alert.employee_id,
        "task_id": alert.task_id,
        "severity": alert.severity,
        "code": alert.code,
        "message": alert.message,
        "detail": alert.detail,
        "correlation_id": alert.correlation_id,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "created_at": alert.created_at.isoformat(),
    }


class EventLogService:
    @staticmethod
    async def lock_event_sequence(session: AsyncSession) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            await session.execute(text("SELECT pg_advisory_xact_lock(731904271)"))

    @staticmethod
    def record_task_activity(
        session: AsyncSession,
        task: QueueTask,
        event_type: str,
        *,
        actor_type: str,
        detail: dict | None = None,
        created_at: datetime | None = None,
    ) -> Activity:
        timestamp = created_at or utc_now()
        activity_detail = {
            **(detail or {}),
            "task_title": task.title,
            "task_type": task.task_type,
            "parent_task_id": task.parent_task_id,
        }
        activity = Activity(
            id=str(uuid4()),
            employee_id=task.employee_id,
            task_id=task.id,
            event_type=event_type,
            title=TASK_ACTIVITY_TITLES[event_type],
            detail=activity_detail,
            correlation_id=task.correlation_id,
            created_at=timestamp,
        )
        audit = AuditLog(
            actor_type=actor_type,
            action=event_type,
            resource_type="task",
            resource_id=task.id,
            detail={"status": task.status, **activity_detail},
            correlation_id=task.correlation_id,
            created_at=timestamp,
        )
        event = RealtimeEvent(
            event_type="activity.created",
            resource_type="activity",
            resource_id=activity.id,
            payload=_activity_payload(activity),
            correlation_id=task.correlation_id,
            created_at=timestamp,
        )
        session.add_all((activity, audit, event))
        return activity

    @staticmethod
    def record_task_alert(
        session: AsyncSession,
        task: QueueTask,
        *,
        severity: str,
        code: str,
        message: str,
        detail: dict | None = None,
        created_at: datetime | None = None,
    ) -> Alert:
        timestamp = created_at or utc_now()
        alert = Alert(
            id=str(uuid4()),
            employee_id=task.employee_id,
            task_id=task.id,
            severity=severity,
            code=code,
            message=message,
            detail=detail or {},
            correlation_id=task.correlation_id,
            created_at=timestamp,
        )
        event = RealtimeEvent(
            event_type="alert.created",
            resource_type="alert",
            resource_id=alert.id,
            payload=_alert_payload(alert),
            correlation_id=task.correlation_id,
            created_at=timestamp,
        )
        session.add_all((alert, event))
        return alert

    @staticmethod
    async def list_activities(
        session: AsyncSession,
        *,
        employee_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[Activity]:
        statement = select(Activity).order_by(Activity.created_at.desc(), Activity.id.desc()).limit(limit)
        if employee_id is not None:
            statement = statement.where(Activity.employee_id == employee_id)
        if task_id is not None:
            statement = statement.where(Activity.task_id == task_id)
        return list((await session.scalars(statement)).all())

    @staticmethod
    async def list_alerts(
        session: AsyncSession,
        *,
        employee_id: str | None = None,
        unresolved_only: bool = True,
        limit: int = 50,
    ) -> list[Alert]:
        statement = select(Alert).order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit)
        if employee_id is not None:
            statement = statement.where(Alert.employee_id == employee_id)
        if unresolved_only:
            statement = statement.where(Alert.resolved_at.is_(None))
        return list((await session.scalars(statement)).all())

    @staticmethod
    async def list_events(session: AsyncSession, *, after_id: int, limit: int = 100) -> list[RealtimeEvent]:
        statement = (
            select(RealtimeEvent)
            .where(RealtimeEvent.id > after_id)
            .order_by(RealtimeEvent.id)
            .limit(limit)
        )
        return list((await session.scalars(statement)).all())

    @staticmethod
    async def latest_event_id(session: AsyncSession) -> int:
        return int(await session.scalar(select(func.coalesce(func.max(RealtimeEvent.id), 0))) or 0)
