from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import EmployeeConnection, Project, QueueTask
from ..schemas import ProjectPlanResponse, TaskCreate
from .local_employee_profiles import LOCAL_EMPLOYEE_PROFILES
from .event_log import EventLogService
from .tasks import task_request_hash


TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
UNAVAILABLE_CONNECTION_STATUSES = {"not_connected", "setup_required", "error", "stopped"}


class ProjectWorkflowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProjectWorkflow:
    status: str
    plan_task: QueueTask
    assignments: list[QueueTask]
    discussions: list[QueueTask]
    synthesis: QueueTask | None
    continuation: QueueTask | None


def _request_hash(employee_id: str, task_type: str, title: str, payload: dict) -> str:
    return task_request_hash(TaskCreate(
        employee_id=employee_id,
        task_type=task_type,
        title=title,
        payload=payload,
    ))


def _safe_result(task: QueueTask) -> dict:
    value = task.result if task.status == "completed" else task.error
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    limit = 12_000
    return {
        "task_id": task.id,
        "employee_id": task.employee_id,
        "title": task.title,
        "status": task.status,
        "content": serialized[:limit],
        "truncated": len(serialized) > limit,
    }


class ProjectWorkflowService:
    @staticmethod
    async def get(session: AsyncSession, plan_task_id: str) -> ProjectWorkflow:
        plan_task = await session.get(QueueTask, plan_task_id)
        if plan_task is None or plan_task.task_type != "project_plan":
            raise ProjectWorkflowError("PROJECT_PLAN_NOT_FOUND", "PM 계획 작업을 찾을 수 없습니다.")
        children = list((await session.scalars(
            select(QueueTask)
            .where(QueueTask.parent_task_id == plan_task_id)
            .order_by(QueueTask.idempotency_key, QueueTask.id)
        )).all())
        assignments = [task for task in children if task.task_type == "employee_task"]
        discussions = [task for task in children if task.task_type == "employee_discussion"]
        syntheses = [task for task in children if task.task_type == "project_synthesis"]
        synthesis = next(
            (task for task in syntheses if task.idempotency_key == f"project-synthesis-final:{plan_task_id}"),
            syntheses[0] if syntheses else None,
        )
        continuation = next((task for task in children if task.task_type == "project_plan"), None)
        plan = plan_task.result.get("plan") if isinstance(plan_task.result, dict) else None
        if continuation is not None:
            status = (
                "continued" if continuation.status == "completed"
                else "failed" if continuation.status in {"failed", "cancelled"}
                else "replanning"
            )
        elif isinstance(plan, dict) and plan.get("needs_user_choice") is True:
            status = "awaiting_user"
        elif discussions and any(task.status not in TERMINAL_TASK_STATUSES for task in discussions):
            status = "discussing"
        elif synthesis is not None:
            status = (
                "completed" if synthesis.status == "completed"
                else "failed" if synthesis.status in {"failed", "cancelled"}
                else "synthesizing"
            )
        else:
            status = "running" if assignments else "not_started"
        return ProjectWorkflow(status, plan_task, assignments, discussions, synthesis, continuation)

    @staticmethod
    async def respond(
        session: AsyncSession, plan_task_id: str, response: ProjectPlanResponse
    ) -> QueueTask:
        await session.scalar(
            select(QueueTask).where(QueueTask.id == plan_task_id).with_for_update()
        )
        current = await ProjectWorkflowService.get(session, plan_task_id)
        task = current.plan_task
        if task.status != "completed" or not isinstance(task.result, dict):
            raise ProjectWorkflowError("PROJECT_PLAN_NOT_COMPLETED", "완료된 PM 계획에만 답할 수 있습니다.")
        plan = task.result.get("plan")
        if not isinstance(plan, dict) or plan.get("needs_user_choice") is not True:
            raise ProjectWorkflowError(
                "PROJECT_PLAN_RESPONSE_NOT_REQUIRED", "PM의 확인 질문이 있는 계획에만 답할 수 있습니다."
            )

        user_response = (
            response.message
            if response.action == "revise"
            else "PM이 제시한 방향을 승인합니다. 제안한 안전안과 범위대로 계속 진행하세요."
        )
        payload = {
            "request": "사용자 답변을 반영해 계획을 다시 세우고, 추가 확인이 필요 없으면 직원들에게 배분하세요.",
            "original_request": task.input_payload,
            "previous_plan": plan,
            "previous_plan_task_id": task.id,
            "response_action": response.action,
            "user_response": user_response,
        }
        title = f"[PM 후속] {task.title}"[:200]
        request_hash = _request_hash("project-manager", "project_plan", title, payload)
        if current.continuation is not None:
            if current.continuation.request_hash == request_hash:
                return current.continuation
            raise ProjectWorkflowError(
                "PROJECT_PLAN_ALREADY_RESPONDED", "이미 이 PM 질문에 답변을 보냈습니다."
            )

        follow_up = QueueTask(
            employee_id="project-manager",
            parent_task_id=task.id,
            task_type="project_plan",
            title=title,
            input_payload=payload,
            # 질문 하나에는 후속 계획 하나만 허용한다. 답변 내용이 달라도 같은 키를 써서
            # SQLite처럼 행 잠금이 약한 환경에서도 두 탭의 동시 응답을 DB가 막게 한다.
            idempotency_key=f"project-plan-response:{task.id}",
            request_hash=request_hash,
            correlation_id=task.correlation_id,
        )
        session.add(follow_up)
        try:
            await session.flush()
            await EventLogService.lock_event_sequence(session)
            EventLogService.record_task_activity(
                session, follow_up, "task.queued", actor_type="user",
                detail={"response_action": response.action},
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raced = await ProjectWorkflowService.get(session, plan_task_id)
            if raced.continuation is not None and raced.continuation.request_hash == request_hash:
                return raced.continuation
            raise ProjectWorkflowError(
                "PROJECT_PLAN_ALREADY_RESPONDED", "이미 이 PM 질문에 답변을 보냈습니다."
            )
        await session.refresh(follow_up)
        return follow_up

    @staticmethod
    async def dispatch(
        session: AsyncSession, plan_task_id: str, *, commit: bool = True
    ) -> ProjectWorkflow:
        # 동시에 두 탭에서 실행해도 한 요청만 하위 작업을 만든다.
        await session.scalar(
            select(QueueTask).where(QueueTask.id == plan_task_id).with_for_update()
        )
        current = await ProjectWorkflowService.get(session, plan_task_id)
        if current.assignments:
            return current
        task = current.plan_task
        if task.status != "completed" or not isinstance(task.result, dict):
            raise ProjectWorkflowError("PROJECT_PLAN_NOT_COMPLETED", "완료된 PM 계획만 실행할 수 있습니다.")
        plan = task.result.get("plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("assignments"), list):
            raise ProjectWorkflowError("PROJECT_PLAN_INVALID", "PM 계획 결과 형식이 올바르지 않습니다.")
        if plan.get("needs_user_choice") is True:
            raise ProjectWorkflowError(
                "PROJECT_PLAN_NEEDS_USER_CHOICE",
                str(plan.get("user_question") or "PM의 확인 질문에 먼저 답해 주세요."),
            )

        assignments = plan["assignments"]
        for assignment in assignments:
            employee_id = assignment.get("employee_id") if isinstance(assignment, dict) else None
            if employee_id not in LOCAL_EMPLOYEE_PROFILES:
                raise ProjectWorkflowError("PROJECT_PLAN_INVALID", "배분할 수 없는 담당자가 포함돼 있습니다.")
            connection = await session.get(EmployeeConnection, employee_id)
            if connection is None or connection.status in UNAVAILABLE_CONNECTION_STATUSES:
                raise ProjectWorkflowError(
                    "PROJECT_ASSIGNEE_UNAVAILABLE",
                    f"현재 작업할 수 없는 직원이 포함돼 있습니다: {employee_id}",
                )
        settings = get_settings()
        development_project: Project | None = None
        if any(LOCAL_EMPLOYEE_PROFILES[item["employee_id"]].mode == "development" for item in assignments):
            if not settings.development_mode_enabled:
                raise ProjectWorkflowError(
                    "PROJECT_ASSIGNEE_UNAVAILABLE", "개발팀 안전 잠금이 해제되지 않았습니다."
                )
            development_project = await session.scalar(select(Project).where(
                Project.repository_root == str(settings.development_repository_root.resolve()),
                Project.project_path == settings.development_project_path,
                Project.status == "active",
            ))
            if development_project is None:
                raise ProjectWorkflowError(
                    "PROJECT_NOT_REGISTERED", "개발 작업을 묶을 프로젝트가 준비되지 않았습니다."
                )

        created: list[QueueTask] = []
        for index, assignment in enumerate(assignments, start=1):
            employee_id = assignment["employee_id"]
            payload = {
                "request": assignment["task"],
                "expected_output": assignment["expected_output"],
                "project_objective": plan.get("objective"),
                "completion_criteria": plan.get("completion_criteria", []),
                "pm_plan_task_id": task.id,
            }
            title = f"[PM 배분] {assignment['task']}"[:200]
            profile = LOCAL_EMPLOYEE_PROFILES[employee_id]
            child = QueueTask(
                employee_id=employee_id,
                project_id=development_project.id if profile.mode == "development" else None,
                parent_task_id=task.id,
                task_type="employee_task",
                title=title,
                input_payload=payload,
                idempotency_key=f"project-dispatch:{task.id}:{index}:{employee_id}",
                request_hash=_request_hash(employee_id, "employee_task", title, payload),
                correlation_id=task.correlation_id,
            )
            session.add(child)
            created.append(child)
        await session.flush()
        await EventLogService.lock_event_sequence(session)
        for child in created:
            EventLogService.record_task_activity(session, child, "task.queued", actor_type="user")
        if not commit:
            return await ProjectWorkflowService.get(session, plan_task_id)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raced = await ProjectWorkflowService.get(session, plan_task_id)
            if raced.assignments:
                return raced
            raise
        return await ProjectWorkflowService.get(session, plan_task_id)

    @staticmethod
    async def advance_after_terminal(session: AsyncSession, terminal_task: QueueTask) -> None:
        if terminal_task.parent_task_id is None or terminal_task.task_type not in {
            "employee_task", "employee_discussion", "project_synthesis",
        }:
            return
        # 마지막 두 작업이 동시에 끝나는 경우 둘 다 상대를 running 으로 보는 것을 막는다.
        await session.scalar(select(QueueTask).where(
            QueueTask.id == terminal_task.parent_task_id
        ).with_for_update())
        plan_task = await session.get(QueueTask, terminal_task.parent_task_id)
        if plan_task is None:
            return

        if terminal_task.task_type == "employee_task":
            assignments = list((await session.scalars(select(QueueTask).where(
                QueueTask.parent_task_id == plan_task.id,
                QueueTask.task_type == "employee_task",
            ))).all())
            if not assignments or any(task.status not in TERMINAL_TASK_STATUSES for task in assignments):
                return
            await ProjectWorkflowService._create_synthesis(
                session, plan_task, assignments, [], phase="conflict_check"
            )
            return

        if terminal_task.task_type == "project_synthesis":
            if terminal_task.idempotency_key != f"project-synthesis:{plan_task.id}":
                return
            report = (terminal_task.result or {}).get("report")
            if not isinstance(report, dict) or report.get("needs_discussion") is not True:
                return
            await ProjectWorkflowService._create_discussions(session, plan_task, report)
            return

        discussions = list((await session.scalars(select(QueueTask).where(
            QueueTask.parent_task_id == plan_task.id,
            QueueTask.task_type == "employee_discussion",
        ))).all())
        if not discussions or any(task.status not in TERMINAL_TASK_STATUSES for task in discussions):
            return
        assignments = list((await session.scalars(select(QueueTask).where(
            QueueTask.parent_task_id == plan_task.id,
            QueueTask.task_type == "employee_task",
        ))).all())
        await ProjectWorkflowService._create_synthesis(
            session, plan_task, assignments, discussions, phase="final"
        )

    @staticmethod
    async def _create_synthesis(
        session: AsyncSession,
        plan_task: QueueTask,
        assignments: list[QueueTask],
        discussions: list[QueueTask],
        *,
        phase: str,
    ) -> None:
        synthesis_key = (
            f"project-synthesis:{plan_task.id}"
            if phase == "conflict_check"
            else f"project-synthesis-final:{plan_task.id}"
        )
        existing = await session.scalar(select(QueueTask).where(QueueTask.idempotency_key == synthesis_key))
        if existing is not None:
            return
        payload = {
            "plan": (plan_task.result or {}).get("plan"),
            "assignment_results": [_safe_result(task) for task in assignments],
            "discussion_results": [_safe_result(task) for task in discussions],
            "synthesis_phase": phase,
            "pm_plan_task_id": plan_task.id,
        }
        title_prefix = "[PM 의견 비교]" if phase == "conflict_check" else "[PM 최종 종합]"
        synthesis = QueueTask(
            employee_id="project-manager",
            parent_task_id=plan_task.id,
            task_type="project_synthesis",
            title=f"{title_prefix} {plan_task.title}"[:200],
            input_payload=payload,
            idempotency_key=synthesis_key,
            request_hash=hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest(),
            correlation_id=plan_task.correlation_id,
        )
        session.add(synthesis)
        await session.flush()
        await EventLogService.lock_event_sequence(session)
        EventLogService.record_task_activity(session, synthesis, "task.queued", actor_type="system")

    @staticmethod
    async def _create_discussions(
        session: AsyncSession, plan_task: QueueTask, report: dict
    ) -> None:
        conflicts = report.get("conflicts")
        if not isinstance(conflicts, list):
            return
        assignments = list((await session.scalars(select(QueueTask).where(
            QueueTask.parent_task_id == plan_task.id,
            QueueTask.task_type == "employee_task",
        ))).all())
        assigned_ids = {task.employee_id for task in assignments}
        selected_ids = sorted({
            employee_id
            for conflict in conflicts if isinstance(conflict, dict)
            for employee_id in conflict.get("employee_ids", [])
            if isinstance(employee_id, str) and employee_id in assigned_ids
        })
        if len(selected_ids) < 2:
            return
        assignment_results = [_safe_result(task) for task in assignments]
        created: list[QueueTask] = []
        for employee_id in selected_ids:
            relevant_conflicts = [
                conflict for conflict in conflicts
                if isinstance(conflict, dict) and employee_id in conflict.get("employee_ids", [])
            ]
            payload = {
                "request": "다른 직원의 결과와 쟁점을 검토하고 동의·반박·수정 의견을 근거와 함께 제시하세요.",
                "expected_output": "쟁점별 입장, 상대 근거 검토, 수정 여부, 최종 제안",
                "meeting_round": "rebuttal",
                "conflicts": relevant_conflicts,
                "assignment_results": assignment_results,
                "pm_plan_task_id": plan_task.id,
            }
            title = f"[PM 토론] {', '.join(str(item.get('topic', '의견 충돌')) for item in relevant_conflicts)}"[:200]
            discussion = QueueTask(
                employee_id=employee_id,
                parent_task_id=plan_task.id,
                task_type="employee_discussion",
                title=title,
                input_payload=payload,
                idempotency_key=f"project-discussion:{plan_task.id}:{employee_id}",
                request_hash=_request_hash(employee_id, "employee_discussion", title, payload),
                correlation_id=plan_task.correlation_id,
            )
            session.add(discussion)
            created.append(discussion)
        await session.flush()
        await EventLogService.lock_event_sequence(session)
        for discussion in created:
            EventLogService.record_task_activity(
                session, discussion, "task.queued", actor_type="system",
                detail={"meeting_round": "rebuttal"},
            )
