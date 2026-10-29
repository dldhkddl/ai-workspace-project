import subprocess
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import Approval, Project, QueueTask
from ..schemas import (
    ApprovalDecision,
    ApprovalDetailResponse,
    ApprovalStatus,
    ApprovalSummaryResponse,
)
from ..services import approval_service
from ..services.project_registry import build_git_executor


router = APIRouter(prefix="/approvals", tags=["approvals"])

# 상태 전이 실패는 대부분 '화면이 오래된 상태를 보고 있다'는 뜻이라 409 로 모은다.
ERROR_STATUS = {
    "APPROVAL_NOT_FOUND": 404,
    "TARGET_UNREADABLE": 503,
    "PROJECT_NOT_BOUND": 503,
}


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def approval_error(error: approval_service.ApprovalError) -> JSONResponse:
    return error_response(ERROR_STATUS.get(error.code, 409), error.code, error.message)


def summary(approval: Approval) -> ApprovalSummaryResponse:
    return ApprovalSummaryResponse.model_validate(approval)


async def detail(session: AsyncSession, approval: Approval) -> ApprovalDetailResponse:
    task = await session.get(QueueTask, approval.task_id)
    result = (task.result or {}) if task is not None else {}

    def string_list(key: str) -> list[str]:
        value = result.get(key)
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    def text(key: str) -> str | None:
        value = result.get(key)
        return value if isinstance(value, str) else None

    return ApprovalDetailResponse(
        **summary(approval).model_dump(),
        base_sha=approval.base_sha,
        result_tree=approval.result_tree,
        impact_hash=approval.impact_hash,
        target_branch=approval.target_branch,
        target_sha_at_approval=approval.target_sha_at_approval,
        applied_sha=approval.applied_sha,
        invalidated_reason=approval.invalidated_reason,
        last_apply_error=approval.last_apply_error,
        apply_attempt=approval.apply_attempt,
        actor_id=approval.actor_id,
        sensitive_files=list(approval.sensitive_files or []),
        task_title=task.title if task is not None else None,
        employee_id=task.employee_id if task is not None else None,
        changed_files=string_list("changed_files"),
        secret_scan_skipped_files=string_list("secret_scan_skipped_files"),
        diff_stat=text("diff_stat"),
        patch_preview=text("patch_preview"),
        patch_preview_truncated=result.get("patch_preview_truncated") is True,
    )


async def load(session: AsyncSession, approval_id: str) -> Approval | None:
    return await session.get(Approval, approval_id)


def read_target(project: Project) -> tuple[str, str]:
    """승인 대상 브랜치와 HEAD 는 서버가 Git 에서 직접 읽는다.

    검증(대상 일치·민감 파일·만료·정책)이 끝난 뒤에 호출되도록 서비스에 게으른
    호출자로 넘긴다. 미리 읽어 두면 저장소를 못 읽는 상황이 그 검사들을 가린다.
    저장소 폴더 자체가 없으면 subprocess 가 예외를 던지므로 여기서 흡수한다.
    """
    git = build_git_executor(get_settings())
    root = Path(project.repository_root)
    try:
        branch = git.run(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if branch.returncode != 0:
            return "", ""
        head = git.run(root, "rev-parse", "HEAD", check=False)
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if head.returncode != 0:
        return "", ""
    return branch.stdout.strip(), head.stdout.strip()


@router.get("", response_model=list[ApprovalSummaryResponse])
async def list_approvals(
    status: ApprovalStatus | None = Query(default=None),
    project_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[ApprovalSummaryResponse]:
    statement = (
        sa.select(Approval)
        .order_by(Approval.created_at.desc(), Approval.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        statement = statement.where(Approval.status == status)
    if project_id is not None:
        statement = statement.where(Approval.project_id == project_id)
    return [summary(row) for row in (await session.scalars(statement)).all()]


@router.get("/{approval_id}", response_model=ApprovalDetailResponse)
async def get_approval(
    approval_id: str, session: AsyncSession = Depends(get_session)
) -> ApprovalDetailResponse | JSONResponse:
    approval = await load(session, approval_id)
    if approval is None:
        return error_response(404, "APPROVAL_NOT_FOUND", "승인 요청을 찾을 수 없습니다.")
    return await detail(session, approval)


@router.post("/{approval_id}/approve", response_model=ApprovalDetailResponse)
async def approve_approval(
    approval_id: str,
    decision: ApprovalDecision,
    session: AsyncSession = Depends(get_session),
) -> ApprovalDetailResponse | JSONResponse:
    approval = await load(session, approval_id)
    if approval is None:
        return error_response(404, "APPROVAL_NOT_FOUND", "승인 요청을 찾을 수 없습니다.")
    if await session.get(Project, approval.project_id) is None:
        return error_response(503, "PROJECT_NOT_BOUND", "프로젝트를 찾을 수 없습니다.")

    try:
        approved = await approval_service.approve(
            session, approval_id,
            result_sha=decision.result_sha, revision=decision.revision,
            resolve_target=read_target,
            acknowledge_sensitive=decision.acknowledge_sensitive,
        )
    except approval_service.ApprovalError as error:
        return approval_error(error)
    return await detail(session, approved)


@router.post("/{approval_id}/reject", response_model=ApprovalDetailResponse)
async def reject_approval(
    approval_id: str,
    decision: ApprovalDecision,
    session: AsyncSession = Depends(get_session),
) -> ApprovalDetailResponse | JSONResponse:
    try:
        rejected = await approval_service.reject(
            session, approval_id, result_sha=decision.result_sha, revision=decision.revision
        )
    except approval_service.ApprovalError as error:
        return approval_error(error)
    return await detail(session, rejected)


@router.post("/{approval_id}/retry", response_model=ApprovalDetailResponse)
async def retry_approval(
    approval_id: str,
    decision: ApprovalDecision,
    session: AsyncSession = Depends(get_session),
) -> ApprovalDetailResponse | JSONResponse:
    """원본을 정리한 뒤 막힌 적용을 다시 시도한다."""
    try:
        retried = await approval_service.retry_apply(
            session, approval_id, result_sha=decision.result_sha, revision=decision.revision
        )
    except approval_service.ApprovalError as error:
        return approval_error(error)
    return await detail(session, retried)
