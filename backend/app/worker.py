import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings, get_settings
from .db import SessionLocal
from pathlib import Path

from .models import Approval, Project, QueueTask
from .services.task_queue import TaskQueueService
from .services.task_errors import TaskHandlerError
from .services.openai_general_manager import OpenAIGeneralManagerHandler
from .services.openai_project_manager import OpenAIProjectManagerHandler
from .services.codex_managers import CodexGeneralManagerHandler, CodexProjectManagerHandler
from .services.general_manager_connection import GeneralManagerConnectionService
from .services.project_manager_connection import ProjectManagerConnectionService
from .services.codex_managers import LocalCodexRunner
from .services.development_workspace import DevelopmentWorkspaceRunner, prune_expired_artifacts
from .services.local_employee_connection import LocalEmployeeConnectionService
from .services.local_employee_profiles import LOCAL_EMPLOYEE_PROFILES
from .services.local_employees import DEVELOPMENT_MODE_PAUSED, LocalEmployeeHandler
from .services.project_registry import (
    ProjectLocation,
    build_git_executor,
    register_configured_project,
)
from .services.approval_apply import ApplyRequest, ApprovalApplier
from .services import approval_service
from .services.project_workflow import ProjectWorkflowError, ProjectWorkflowService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskContext:
    id: str
    employee_id: str
    task_type: str
    title: str
    payload: dict[str, Any]
    correlation_id: str
    attempt_count: int
    max_attempts: int = 1
    project_id: str | None = None
    # 실행 직전에 DB 의 프로젝트 행에서 채운다. 전역 설정을 쓰면 프로젝트가
    # 둘 이상 됐을 때 A 프로젝트 작업이 B 프로젝트에서 실행될 수 있다.
    project: ProjectLocation | None = None

    @classmethod
    def from_task(cls, task: QueueTask) -> "TaskContext":
        return cls(
            id=task.id,
            employee_id=task.employee_id,
            task_type=task.task_type,
            title=task.title,
            payload=task.input_payload,
            correlation_id=task.correlation_id,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            project_id=task.project_id,
        )


TaskHandler = Callable[[TaskContext], Awaitable[dict]]
ConnectionUpdate = Callable[[AsyncSession], Awaitable[None]]


async def _best_effort_connection_update(
    session_factory: async_sessionmaker[AsyncSession],
    update: ConnectionUpdate,
) -> None:
    try:
        async with session_factory() as session:
            await update(session)
    except Exception:
        logger.warning("AI employee connection status update failed")


def build_connection_tracked_handler(
    handler: TaskHandler,
    session_factory: async_sessionmaker[AsyncSession],
    connection_service: Any,
    *,
    error_prefixes: tuple[str, ...],
) -> TaskHandler:
    source = getattr(handler, "source", connection_service.SOURCE)

    async def handle(context: TaskContext) -> dict:
        try:
            result = await handler(context)
        except TaskHandlerError as exc:
            if exc.code.startswith(error_prefixes):
                async def mark_failure(session: AsyncSession) -> None:
                    await connection_service.mark_failure(
                        session,
                        code=exc.code,
                        message=exc.message,
                        retryable=exc.retryable,
                        source=source,
                    )

                await _best_effort_connection_update(session_factory, mark_failure)
            raise

        async def mark_success(session: AsyncSession) -> None:
            await connection_service.mark_success(session, source=source)

        await _best_effort_connection_update(session_factory, mark_success)
        return result

    return handle


def build_general_manager_handler(
    general_manager: TaskHandler,
    session_factory: async_sessionmaker[AsyncSession],
) -> TaskHandler:
    return build_connection_tracked_handler(
        general_manager,
        session_factory,
        GeneralManagerConnectionService,
        error_prefixes=("OPENAI_", "CODEX_"),
    )


def build_project_manager_handler(
    project_manager: TaskHandler,
    session_factory: async_sessionmaker[AsyncSession],
) -> TaskHandler:
    return build_connection_tracked_handler(
        project_manager,
        session_factory,
        ProjectManagerConnectionService,
        error_prefixes=("OPENAI_", "CODEX_", "AGENT_"),
    )


def build_openai_role_handlers(
    settings: Settings,
) -> tuple[OpenAIGeneralManagerHandler, OpenAIProjectManagerHandler]:
    general_manager = OpenAIGeneralManagerHandler(
        api_key=settings.openai_api_key,
        model=settings.openai_general_manager_model,
        reasoning_effort=settings.openai_general_manager_reasoning_effort,
        max_output_tokens=settings.openai_max_output_tokens,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    project_manager = OpenAIProjectManagerHandler(
        api_key=settings.openai_api_key,
        model=settings.openai_project_manager_model,
        reasoning_effort=settings.openai_project_manager_reasoning_effort,
        agent_path=settings.agent_memory_root / ".claude" / "agents" / "agents-orchestrator.md",
        max_output_tokens=settings.openai_max_output_tokens,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    return general_manager, project_manager


def build_codex_role_handlers(
    settings: Settings,
) -> tuple[CodexGeneralManagerHandler, CodexProjectManagerHandler]:
    general_manager = CodexGeneralManagerHandler(
        model=settings.codex_general_manager_model,
        reasoning_effort=settings.codex_general_manager_reasoning_effort,
        timeout_seconds=settings.codex_timeout_seconds,
    )
    project_manager = CodexProjectManagerHandler(
        model=settings.codex_project_manager_model,
        reasoning_effort=settings.codex_project_manager_reasoning_effort,
        agent_path=settings.agent_memory_root / ".claude" / "agents" / "agents-orchestrator.md",
        timeout_seconds=settings.codex_timeout_seconds,
    )
    return general_manager, project_manager


def build_role_handlers(settings: Settings):
    if settings.manager_ai_runtime == "codex":
        return build_codex_role_handlers(settings)
    return build_openai_role_handlers(settings)


def build_development_runner_factory(settings: Settings):
    """작업에 묶인 프로젝트 위치로 실행기를 만든다.

    전역 설정 하나로 만들어 두면 프로젝트가 둘 이상 등록됐을 때
    A 프로젝트 작업이 B 프로젝트 저장소에서 실행될 수 있다.
    """
    def factory(location: ProjectLocation) -> DevelopmentWorkspaceRunner:
        return DevelopmentWorkspaceRunner(
            repository_root=location.repository_root,
            worktree_root=settings.development_worktree_root / location.id,
            model=settings.codex_employee_model,
            reasoning_effort=settings.codex_development_reasoning_effort,
            timeout_seconds=settings.codex_development_timeout_seconds,
            project_path=location.project_path,
            artifact_root=settings.development_artifact_root,
            artifact_retention_days=settings.development_artifact_retention_days,
            patch_preview_limit=settings.development_patch_preview_limit,
            allow_repository_scope=settings.development_allow_repository_scope,
            dependency_paths=location.dependency_paths,
            commit_author_name=settings.development_commit_author_name,
            commit_author_email=settings.development_commit_author_email,
        )

    return factory



def build_approval_recorder(session_factory: async_sessionmaker[AsyncSession]):
    """작업 완료 트랜잭션 안에서 승인을 만드는 콜백."""

    async def record(session: AsyncSession, task: QueueTask, result: dict) -> None:
        if task.task_type == "project_plan":
            try:
                await ProjectWorkflowService.dispatch(session, task.id, commit=False)
            except ProjectWorkflowError as error:
                if error.code == "PROJECT_PLAN_NEEDS_USER_CHOICE":
                    return
                raise TaskHandlerError(error.code, error.message, retryable=False) from error
            return
        if task.task_type != "employee_task":
            return
        try:
            await approval_service.create_for_task(session, task, result)
        except approval_service.ApprovalError as error:
            # 결과 형식이 잘못된 경우다. 다시 실행해도 같은 결과가 나온다.
            raise TaskHandlerError(error.code, error.message, retryable=False) from error

    return record


def build_approval_apply_handler(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> TaskHandler:
    """승인된 변경을 원본에 반영한다.

    원본 재검사부터 상태·감사 기록까지를 하나의 트랜잭션(=프로젝트 잠금 구간)으로 묶는다.
    중간에 커밋하면 잠금이 풀려 다른 승인이 같은 원본을 각자 기준으로 옮길 수 있다.
    """

    async def handle(context: TaskContext) -> dict:
        approval_id = context.payload.get("approval_id")
        if not isinstance(approval_id, str):
            raise TaskHandlerError("APPROVAL_NOT_FOUND", "승인 정보가 없습니다.", retryable=False)

        # applying 표시는 잠금 밖에서 따로 커밋한다. 잠금 트랜잭션 안에서 바꾸면
        # 도중에 실패했을 때 함께 되돌아가, '적용 중이었다'는 사실이 남지 않는다.
        async with session_factory() as session:
            approval = await approval_service.begin_apply(session, approval_id)
            if approval.status == "applied":
                return {"status": "already_applied", "applied_sha": approval.applied_sha}
            project_id = approval.project_id

        async with session_factory() as session:
            # 원본 재검사부터 상태·감사 기록까지가 한 잠금 구간이다.
            await approval_service.acquire_project_lock(session, project_id)
            approval = await session.get(Approval, approval_id)
            if approval is None:
                raise TaskHandlerError("APPROVAL_NOT_FOUND", "승인을 찾을 수 없습니다.", retryable=False)
            if approval.status == "applied":
                return {"status": "already_applied", "applied_sha": approval.applied_sha}

            project = await session.get(Project, approval.project_id)
            if project is None or project.status != "active":
                raise TaskHandlerError(
                    "PROJECT_NOT_ACTIVE", "중지된 프로젝트입니다.", retryable=False
                )

            applier = ApprovalApplier(
                repository_root=Path(project.repository_root),
                integration_root=settings.development_worktree_root / project.id / "integration",
                git=build_git_executor(settings),
            )
            branch = approval.target_branch or ""

            # 재시도도 리허설과 영향 해시 검사를 그대로 거친다. 조상 관계만 보면
            # 누가 수동으로 병합한 뒤 더 고친 경우를 걸러내지 못한다.
            # 이미 반영돼 있으면 병합이 no-op 이 되어 그대로 통과한다.
            request = ApplyRequest(
                approval_id=approval.id,
                target_branch=branch,
                result_sha=approval.result_sha,
                scope=approval.impact_scope,
                expected_impact_hash=approval.impact_hash,
            )
            rehearsal = await asyncio.to_thread(applier.rehearse, request)

            if rehearsal.status != "ready":
                await asyncio.to_thread(applier.discard, rehearsal)
                reason = {
                    "needs_reapproval": "IMPACT_CHANGED",
                    "conflict": "MERGE_CONFLICT",
                    "failed": "INTEGRATION_FAILED",
                }[rehearsal.status]
                await approval_service.invalidate(session, approval, reason, commit=False)
                await session.commit()
                raise TaskHandlerError(
                    f"APPLY_{reason}", rehearsal.message or "적용을 중지했습니다.", retryable=False
                )

            outcome = await asyncio.to_thread(applier.apply, rehearsal)
            if outcome.status == "target_dirty":
                await asyncio.to_thread(applier.discard, rehearsal)
                detail = ", ".join(outcome.blocking_files[:5])
                # 몇 초 안에 자동으로 반복하면 사용자가 파일을 정리할 틈이 없다.
                await approval_service.block_apply(
                    session, approval, f"{outcome.message} ({detail})", commit=False
                )
                await session.commit()
                raise TaskHandlerError(
                    "APPLY_TARGET_DIRTY", outcome.message, retryable=False
                )
            if outcome.status != "applied":
                await asyncio.to_thread(applier.discard, rehearsal)
                # 원본이 그 사이 움직인 경우만 자동으로 다시 통합한다.
                # 재시도가 소진되면 applying 에 남기지 않고 사용자에게 넘긴다.
                can_retry = (
                    outcome.status == "target_moved"
                    and context.attempt_count < context.max_attempts
                )
                if not can_retry:
                    await approval_service.block_apply(
                        session, approval, outcome.message or "원본에 반영하지 못했습니다.",
                        commit=False,
                    )
                await session.commit()
                raise TaskHandlerError(
                    f"APPLY_{outcome.status.upper()}",
                    outcome.message or "원본에 반영하지 못했습니다.",
                    retryable=can_retry,
                )

            await approval_service.mark_applied(session, approval, outcome.applied_sha, commit=False)
            await session.commit()
            return {
                "status": "applied",
                "applied_sha": outcome.applied_sha,
                "approval_id": approval.id,
                "changed_files": rehearsal.changed_files,
            }

    return handle


def build_workspace_cleanup_handler(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> TaskHandler:
    """거절한 작업의 분리 작업공간만 치운다.

    거절 자체는 API 에서 이미 끝났다. 여기서 실패해도 거절이 되돌아가지 않는다.
    브랜치는 지우지 않고 `rejected/` 로 옮겨 보관한다 — 거절한 작업이 추가 수정
    요청의 출발점인 경우가 많고, ref 를 지우면 패치 객체도 gc 대상이 된다.
    """

    def clean(repository_root: Path, workspace: str | None, branch: str | None) -> list[str]:
        git = build_git_executor(settings)
        done: list[str] = []
        if workspace:
            git.run(repository_root, "worktree", "remove", "--force", workspace, check=False)
            done.append("worktree")
        if branch and not branch.startswith("rejected/"):
            moved = git.run(repository_root, "branch", "-m", branch, f"rejected/{branch}", check=False)
            if moved.returncode == 0:
                done.append(f"rejected/{branch}")
        git.run(repository_root, "worktree", "prune", check=False)
        return done

    async def handle(context: TaskContext) -> dict:
        approval_id = context.payload.get("approval_id")
        async with session_factory() as session:
            approval = await session.get(Approval, approval_id) if isinstance(approval_id, str) else None
            if approval is None:
                raise TaskHandlerError(
                    "APPROVAL_NOT_FOUND", "정리할 승인을 찾을 수 없습니다.", retryable=False
                )
            project = await session.get(Project, approval.project_id)
            task = await session.get(QueueTask, approval.task_id)
            if project is None:
                raise TaskHandlerError(
                    "PROJECT_NOT_BOUND", "프로젝트를 찾을 수 없습니다.", retryable=False
                )
            workspace = (task.result or {}).get("workspace") if task is not None else None

        done = await asyncio.to_thread(
            clean, Path(project.repository_root), workspace, approval.branch
        )
        return {"status": "cleaned", "done": done}



def build_local_employee_handler(settings: Settings) -> LocalEmployeeHandler:
    return LocalEmployeeHandler(
        agent_root=settings.agent_memory_root / ".claude" / "agents",
        model=settings.codex_employee_model,
        reasoning_effort=settings.codex_employee_reasoning_effort,
        analysis_runner=LocalCodexRunner(timeout_seconds=settings.codex_timeout_seconds),
        development_enabled=settings.development_mode_enabled,
        development_runner_factory=build_development_runner_factory(settings),
    )


async def resolve_task_project(
    session_factory: async_sessionmaker[AsyncSession], context: TaskContext
) -> TaskContext:
    if context.project_id is None:
        return context
    async with session_factory() as session:
        project = await session.get(Project, context.project_id)
        if project is None:
            raise TaskHandlerError(
                "PROJECT_NOT_BOUND", "작업에 묶인 프로젝트를 찾을 수 없습니다.", retryable=False
            )
        if project.status != "active":
            raise TaskHandlerError(
                "PROJECT_NOT_ACTIVE", "중지된 프로젝트의 작업은 실행하지 않습니다.", retryable=False
            )
        return replace(context, project=ProjectLocation.from_project(project))


def build_local_employee_tracked_handler(
    handler: LocalEmployeeHandler,
    session_factory: async_sessionmaker[AsyncSession],
) -> TaskHandler:
    async def handle(context: TaskContext) -> dict:
        source = handler.source_for(context.employee_id)
        try:
            context = await resolve_task_project(session_factory, context)
            result = await handler(context)
        except TaskHandlerError as exc:
            if exc.code.startswith(("CODEX_", "AGENT_", "WORKSPACE_")):
                async def mark_failure(session: AsyncSession) -> None:
                    await LocalEmployeeConnectionService.mark_failure(
                        session,
                        context.employee_id,
                        source=source,
                        code=exc.code,
                        message=exc.message,
                        retryable=exc.retryable,
                    )
                await _best_effort_connection_update(session_factory, mark_failure)
            raise

        async def mark_success(session: AsyncSession) -> None:
            await LocalEmployeeConnectionService.mark_success(
                session, context.employee_id, source=source
            )
        await _best_effort_connection_update(session_factory, mark_success)
        return result

    return handle


class TaskWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str,
        handlers: dict[str, TaskHandler],
        *,
        lease_seconds: float = 60,
        heartbeat_seconds: float = 10,
        task_timeout_seconds: float = 300,
        on_completed: Callable[..., Awaitable[None]] | None = None,
        on_terminal: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        if heartbeat_seconds <= 0 or lease_seconds <= heartbeat_seconds or task_timeout_seconds <= 0:
            raise ValueError("worker timing settings are invalid")
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.handlers = handlers
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.task_timeout_seconds = task_timeout_seconds
        self.on_completed = on_completed
        self.on_terminal = on_terminal

    async def run_once(self) -> bool:
        async with self.session_factory() as session:
            recovered = await TaskQueueService.recover_expired(session)
        async with self.session_factory() as session:
            task = await TaskQueueService.claim_next(
                session,
                self.worker_id,
                set(self.handlers),
                self.lease_seconds,
            )
        if task is None:
            return recovered > 0

        handler = self.handlers[task.task_type]
        execution = asyncio.create_task(handler(TaskContext.from_task(task)))
        deadline = asyncio.get_running_loop().time() + self.task_timeout_seconds

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await self._stop_execution(execution)
                await self._fail(
                    task.id,
                    {"code": "TASK_TIMEOUT", "message": "작업 제한 시간을 초과했습니다."},
                )
                return True

            done, _ = await asyncio.wait(
                {execution},
                timeout=min(self.heartbeat_seconds, remaining),
            )
            if done:
                try:
                    result = execution.result()
                    self._validate_result(result)
                except asyncio.CancelledError:
                    return True
                except TaskHandlerError as exc:
                    await self._fail(
                        task.id,
                        {"code": exc.code, "message": exc.message},
                        retryable=exc.retryable,
                    )
                    return True
                except Exception as exc:
                    await self._fail(
                        task.id,
                        {
                            "code": "TASK_EXECUTION_FAILED",
                            "message": "작업 실행에 실패했습니다.",
                            "type": type(exc).__name__,
                        },
                    )
                    return True
                try:
                    completed = await self._complete(task.id, result)
                except TaskHandlerError as exc:
                    # 결과 형식 문제 등. 작업만 실패시키고 워커는 계속 돈다.
                    await self._fail(
                        task.id, {"code": exc.code, "message": exc.message},
                        retryable=exc.retryable,
                    )
                    return True
                except Exception:
                    logger.exception("작업 완료 처리에 실패했습니다: %s", task.id)
                    await self._fail(
                        task.id,
                        {
                            "code": "TASK_COMPLETION_FAILED",
                            "message": "작업 결과를 저장하지 못했습니다.",
                        },
                        retryable=True,
                    )
                    return True
                if completed:
                    return True
                status = await self._heartbeat(task.id)
                if status == "cancel_requested":
                    await self._mark_cancelled(task.id)
                return True

            status = await self._heartbeat(task.id)
            if status == "cancel_requested":
                await self._stop_execution(execution)
                await self._mark_cancelled(task.id)
                return True
            if status != "running":
                await self._stop_execution(execution)
                return True

    async def run_forever(self, *, stop_event: asyncio.Event | None = None, poll_seconds: float = 1) -> None:
        if poll_seconds <= 0:
            raise ValueError("worker poll setting is invalid")
        stopped = stop_event or asyncio.Event()
        while not stopped.is_set():
            worked = await self.run_once()
            if worked:
                continue
            try:
                await asyncio.wait_for(stopped.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass

    async def _heartbeat(self, task_id: str) -> str | None:
        async with self.session_factory() as session:
            return await TaskQueueService.heartbeat(
                session,
                task_id,
                self.worker_id,
                self.lease_seconds,
            )

    async def _complete(self, task_id: str, result: dict) -> bool:
        async with self.session_factory() as session:
            return await TaskQueueService.complete(
                session, task_id, self.worker_id, result,
                on_completed=self.on_completed, on_terminal=self.on_terminal,
            )

    async def _fail(self, task_id: str, error: dict, *, retryable: bool = True) -> None:
        async with self.session_factory() as session:
            failed = await TaskQueueService.fail(
                session, task_id, self.worker_id, error, retryable=retryable,
                on_terminal=self.on_terminal,
            )
            if failed is None:
                status = await TaskQueueService.heartbeat(
                    session,
                    task_id,
                    self.worker_id,
                    self.lease_seconds,
                )
                if status == "cancel_requested":
                    await TaskQueueService.mark_cancelled(
                        session, task_id, self.worker_id, on_terminal=self.on_terminal
                    )

    async def _mark_cancelled(self, task_id: str) -> None:
        async with self.session_factory() as session:
            await TaskQueueService.mark_cancelled(
                session, task_id, self.worker_id, on_terminal=self.on_terminal
            )

    @staticmethod
    async def _stop_execution(execution: asyncio.Task) -> None:
        execution.cancel()
        try:
            await execution
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    @staticmethod
    def _validate_result(result: dict) -> None:
        if not isinstance(result, dict):
            raise TypeError("task result must be an object")
        json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


async def run_worker() -> None:
    settings = get_settings()
    # 만료일만 기록해 두면 아티팩트가 계속 쌓이므로 시작할 때 한 번 쓸어낸다.
    try:
        prune_expired_artifacts(
            settings.development_artifact_root, settings.development_artifact_retention_days
        )
    except OSError:
        logger.warning("artifact cleanup failed")
    general_manager, project_manager = build_role_handlers(settings)
    local_employees = build_local_employee_handler(settings)

    async with SessionLocal() as session:
        await register_configured_project(session, settings)
        general_manager_error = (
            general_manager.configuration_error()
            if hasattr(general_manager, "configuration_error")
            else None if general_manager.api_key else {
                "code": "OPENAI_NOT_CONFIGURED",
                "message": "OpenAI API 키 설정이 필요합니다.",
            }
        )
        await GeneralManagerConnectionService.mark_configuration(
            session,
            configured=general_manager_error is None,
            source=general_manager.source,
            error=general_manager_error,
        )
        await ProjectManagerConnectionService.mark_configuration(
            session,
            error=project_manager.configuration_error(),
            source=project_manager.source,
        )
        for employee_id in LOCAL_EMPLOYEE_PROFILES:
            employee_error = local_employees.configuration_error(employee_id)
            await LocalEmployeeConnectionService.mark_configuration(
                session,
                employee_id,
                source=local_employees.source_for(employee_id),
                error=employee_error,
                status_on_error="stopped"
                if employee_error == DEVELOPMENT_MODE_PAUSED
                else "setup_required",
            )

    handle_general_manager = build_general_manager_handler(general_manager, SessionLocal)
    handle_project_manager = build_project_manager_handler(project_manager, SessionLocal)
    handle_local_employee = build_local_employee_tracked_handler(local_employees, SessionLocal)

    worker = TaskWorker(
        SessionLocal,
        worker_id=f"{socket.gethostname()}-{os.getpid()}",
        handlers={
            "general_manager_review": handle_general_manager,
            "project_plan": handle_project_manager,
            "project_synthesis": handle_project_manager,
            "employee_task": handle_local_employee,
            "employee_discussion": handle_local_employee,
            approval_service.APPLY_TASK_TYPE: build_approval_apply_handler(SessionLocal, settings),
            approval_service.CLEANUP_TASK_TYPE: build_workspace_cleanup_handler(SessionLocal, settings),
        },
        on_completed=build_approval_recorder(SessionLocal),
        on_terminal=ProjectWorkflowService.advance_after_terminal,
        lease_seconds=settings.worker_lease_seconds,
        heartbeat_seconds=settings.worker_heartbeat_seconds,
        task_timeout_seconds=settings.worker_task_timeout_seconds,
    )
    await worker.run_forever(poll_seconds=settings.worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run_worker())
