from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import QueueTask
from ..schemas import (
    ProjectPlanResponse, ProjectWorkflowResponse, TaskCreate, TaskDetailResponse, TaskStatus,
    TaskSummaryResponse,
)
from ..services.project_workflow import ProjectWorkflowError, ProjectWorkflowService
from ..services.tasks import (
    DevelopmentModePausedError,
    EmployeeNotFoundError,
    ProjectNotActiveError,
    ProjectNotRegisteredError,
    IdempotencyConflictError,
    InvalidTaskAssigneeError,
    TaskNotCancellableError,
    TaskNotFoundError,
    TaskService,
    UnsupportedTaskTypeError,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
VALIDATION_ERROR_RESPONSE = {
    422: {
        "description": "요청 형식 오류",
        "content": {
            "application/json": {
                "example": {"error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}}
            }
        },
    }
}


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def task_summary_response(task: QueueTask, *, has_continuation: bool = False) -> TaskSummaryResponse:
    plan = task.result.get("plan") if isinstance(task.result, dict) else None
    requires_user_response = (
        task.status == "completed"
        and task.task_type == "project_plan"
        and isinstance(plan, dict)
        and plan.get("needs_user_choice") is True
        and not has_continuation
    )
    return TaskSummaryResponse(
        id=task.id,
        employee_id=task.employee_id,
        task_type=task.task_type,
        title=task.title,
        status=task.status,
        correlation_id=task.correlation_id,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        cancel_requested_at=task.cancel_requested_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        requires_user_response=requires_user_response,
    )


def task_detail_response(task: QueueTask, *, has_continuation: bool = False) -> TaskDetailResponse:
    will_retry = task.status == "queued" and task.attempt_count > 0 and task.error is not None
    return TaskDetailResponse(
        **task_summary_response(task, has_continuation=has_continuation).model_dump(),
        payload=task.input_payload,
        result=task.result,
        error=task.error,
        will_retry=will_retry,
    )


def workflow_response(workflow) -> ProjectWorkflowResponse:
    return ProjectWorkflowResponse(
        status=workflow.status,
        plan_task_id=workflow.plan_task.id,
        assignments=[task_summary_response(task) for task in workflow.assignments],
        discussions=[task_summary_response(task) for task in workflow.discussions],
        synthesis=task_detail_response(workflow.synthesis) if workflow.synthesis else None,
        continuation=task_detail_response(workflow.continuation) if workflow.continuation else None,
    )


@router.post("", response_model=TaskDetailResponse, status_code=201, responses=VALIDATION_ERROR_RESPONSE)
async def create_task(
    request: TaskCreate,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=120, pattern=r".*\S.*"),
    ],
    session: AsyncSession = Depends(get_session),
) -> TaskDetailResponse | JSONResponse:
    try:
        result = await TaskService.create(session, request, idempotency_key.strip())
    except UnsupportedTaskTypeError:
        return error_response(422, "UNSUPPORTED_TASK_TYPE", "지원하지 않는 작업 유형입니다.")
    except EmployeeNotFoundError:
        return error_response(404, "EMPLOYEE_NOT_FOUND", "직원을 찾을 수 없습니다.")
    except InvalidTaskAssigneeError:
        return error_response(422, "INVALID_TASK_ASSIGNEE", "작업 유형과 담당 직원이 일치하지 않습니다.")
    except IdempotencyConflictError:
        return error_response(409, "IDEMPOTENCY_KEY_CONFLICT", "같은 멱등성 키가 다른 요청에 사용됐습니다.")
    except DevelopmentModePausedError:
        return error_response(
            403,
            "DEVELOPMENT_MODE_PAUSED",
            "안전 구조 개선 중이라 개발 작업 요청을 잠시 받지 않습니다.",
        )
    except ProjectNotRegisteredError:
        return error_response(
            503,
            "PROJECT_NOT_REGISTERED",
            "개발 작업을 묶을 프로젝트가 등록되지 않았습니다.",
        )
    except ProjectNotActiveError:
        return error_response(
            409,
            "PROJECT_NOT_ACTIVE",
            "중지된 프로젝트에는 작업을 요청할 수 없습니다.",
        )
    if not result.created:
        response.status_code = 200
    return task_detail_response(result.task)


@router.get("", response_model=list[TaskSummaryResponse], responses=VALIDATION_ERROR_RESPONSE)
async def list_tasks(
    status: TaskStatus | None = Query(default=None),
    employee_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[TaskSummaryResponse]:
    tasks = await TaskService.list(session, status, employee_id, limit, offset)
    task_ids = [task.id for task in tasks]
    continuation_parent_ids = set((await session.scalars(select(QueueTask.parent_task_id).where(
        QueueTask.parent_task_id.in_(task_ids),
        QueueTask.task_type == "project_plan",
    ))).all()) if task_ids else set()
    return [task_summary_response(
        task, has_continuation=task.id in continuation_parent_ids
    ) for task in tasks]


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str, session: AsyncSession = Depends(get_session)) -> TaskDetailResponse | JSONResponse:
    task = await TaskService.get(session, task_id)
    if task is None:
        return error_response(404, "TASK_NOT_FOUND", "작업을 찾을 수 없습니다.")
    has_continuation = await session.scalar(select(QueueTask.id).where(
        QueueTask.parent_task_id == task.id,
        QueueTask.task_type == "project_plan",
    ).limit(1)) is not None
    return task_detail_response(task, has_continuation=has_continuation)


@router.post("/{task_id}/cancel", response_model=TaskDetailResponse)
async def cancel_task(task_id: str, session: AsyncSession = Depends(get_session)) -> TaskDetailResponse | JSONResponse:
    try:
        cancelled = await TaskService.cancel(session, task_id)
    except TaskNotFoundError:
        return error_response(404, "TASK_NOT_FOUND", "작업을 찾을 수 없습니다.")
    except TaskNotCancellableError:
        return error_response(409, "TASK_NOT_CANCELLABLE", "현재 상태에서는 작업을 취소할 수 없습니다.")
    return task_detail_response(cancelled)


@router.post("/{task_id}/dispatch", response_model=ProjectWorkflowResponse)
async def dispatch_project_plan(
    task_id: str, session: AsyncSession = Depends(get_session)
) -> ProjectWorkflowResponse | JSONResponse:
    try:
        workflow = await ProjectWorkflowService.dispatch(session, task_id)
    except ProjectWorkflowError as error:
        status = 404 if error.code == "PROJECT_PLAN_NOT_FOUND" else 409
        return error_response(status, error.code, error.message)
    return workflow_response(workflow)


@router.post("/{task_id}/respond", response_model=TaskDetailResponse, responses=VALIDATION_ERROR_RESPONSE)
async def respond_to_project_plan(
    task_id: str,
    request: ProjectPlanResponse,
    session: AsyncSession = Depends(get_session),
) -> TaskDetailResponse | JSONResponse:
    try:
        follow_up = await ProjectWorkflowService.respond(session, task_id, request)
    except ProjectWorkflowError as error:
        status = 404 if error.code == "PROJECT_PLAN_NOT_FOUND" else 409
        return error_response(status, error.code, error.message)
    return task_detail_response(follow_up)


@router.get("/{task_id}/workflow", response_model=ProjectWorkflowResponse)
async def get_project_workflow(
    task_id: str, session: AsyncSession = Depends(get_session)
) -> ProjectWorkflowResponse | JSONResponse:
    try:
        workflow = await ProjectWorkflowService.get(session, task_id)
    except ProjectWorkflowError as error:
        return error_response(404, error.code, error.message)
    return workflow_response(workflow)
