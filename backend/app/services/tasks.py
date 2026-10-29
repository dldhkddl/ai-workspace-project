import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Employee, Project, QueueTask
from ..schemas import TaskCreate, TaskStatus
from .event_log import EventLogService
from .local_employee_profiles import LOCAL_EMPLOYEE_PROFILES


class EmployeeNotFoundError(Exception):
    pass


class IdempotencyConflictError(Exception):
    pass


class TaskNotCancellableError(Exception):
    pass


class TaskNotFoundError(Exception):
    pass


class InvalidTaskAssigneeError(Exception):
    pass


class DevelopmentModePausedError(Exception):
    pass


class ProjectNotRegisteredError(Exception):
    pass


class ProjectNotActiveError(Exception):
    pass


class UnsupportedTaskTypeError(Exception):
    pass


USER_REQUESTABLE_TASK_TYPES = frozenset(
    {
        "general_manager_review",
        "project_plan",
        "employee_task",
    }
)


@dataclass(frozen=True)
class TaskCreationResult:
    task: QueueTask
    created: bool


def task_request_hash(request: TaskCreate) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskService:
    @staticmethod
    async def create(session: AsyncSession, request: TaskCreate, idempotency_key: str) -> TaskCreationResult:
        if request.task_type not in USER_REQUESTABLE_TASK_TYPES:
            raise UnsupportedTaskTypeError

        request_hash = task_request_hash(request)
        existing = await session.scalar(select(QueueTask).where(QueueTask.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError
            return TaskCreationResult(task=existing, created=False)

        if await session.get(Employee, request.employee_id) is None:
            raise EmployeeNotFoundError
        required_assignee = {
            "general_manager_review": "general-manager",
            "project_plan": "project-manager",
        }.get(request.task_type)
        if required_assignee is not None and request.employee_id != required_assignee:
            raise InvalidTaskAssigneeError
        project_id: str | None = None
        if request.task_type == "employee_task":
            profile = LOCAL_EMPLOYEE_PROFILES.get(request.employee_id)
            if profile is None:
                raise InvalidTaskAssigneeError
            if profile.mode == "development":
                settings = get_settings()
                if not settings.development_mode_enabled:
                    raise DevelopmentModePausedError
                # 승인은 프로젝트에 묶인다. 작업이 만들어지는 순간 결속해 둔다.
                project = await session.scalar(
                    select(Project).where(
                        Project.repository_root == str(settings.development_repository_root.resolve()),
                        Project.project_path == settings.development_project_path,
                    )
                )
                if project is None:
                    raise ProjectNotRegisteredError
                if project.status != "active":
                    raise ProjectNotActiveError
                project_id = project.id

        task = QueueTask(
            employee_id=request.employee_id,
            project_id=project_id,
            task_type=request.task_type,
            title=request.title,
            input_payload=request.payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        session.add(task)
        try:
            await session.flush()
            await EventLogService.lock_event_sequence(session)
            EventLogService.record_task_activity(
                session,
                task,
                "task.queued",
                actor_type="user",
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(select(QueueTask).where(QueueTask.idempotency_key == idempotency_key))
            if existing is None:
                raise
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError
            return TaskCreationResult(task=existing, created=False)
        await session.refresh(task)
        return TaskCreationResult(task=task, created=True)

    @staticmethod
    async def list(
        session: AsyncSession,
        status: TaskStatus | None = None,
        employee_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[QueueTask]:
        statement = (
            select(QueueTask)
            .order_by(QueueTask.created_at.desc(), QueueTask.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            statement = statement.where(QueueTask.status == status)
        if employee_id is not None:
            statement = statement.where(QueueTask.employee_id == employee_id)
        return list((await session.scalars(statement)).all())

    @staticmethod
    async def get(session: AsyncSession, task_id: str) -> QueueTask | None:
        return await session.get(QueueTask, task_id)

    @staticmethod
    async def cancel(session: AsyncSession, task_id: str) -> QueueTask:
        now = datetime.now(timezone.utc)
        statement = (
            update(QueueTask)
            .where(QueueTask.id == task_id, QueueTask.status.in_(("queued", "running")))
            .values(
                status=case((QueueTask.status == "queued", "cancelled"), else_="cancel_requested"),
                cancel_requested_at=now,
                finished_at=case((QueueTask.status == "queued", now), else_=QueueTask.finished_at),
                updated_at=now,
            )
            .returning(QueueTask)
        )
        task = (await session.scalars(statement, execution_options={"synchronize_session": False})).one_or_none()
        if task is None:
            existing = await session.get(QueueTask, task_id)
            if existing is None:
                raise TaskNotFoundError
            raise TaskNotCancellableError
        await EventLogService.lock_event_sequence(session)
        EventLogService.record_task_activity(
            session,
            task,
            "task.cancelled" if task.status == "cancelled" else "task.cancel_requested",
            actor_type="user",
            created_at=now,
        )
        if task.status == "cancelled":
            # PM이 배분한 마지막 열린 작업을 사용자가 취소한 경우에도 종합 단계로 넘어간다.
            from .project_workflow import ProjectWorkflowService
            await ProjectWorkflowService.advance_after_terminal(session, task)
        await session.commit()
        return task
