from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ACTIVE_APPROVAL_STATUSES, Approval, Project, QueueTask
from .event_log import EventLogService
from .impact_scope import impact_scope_hash


# 인증이 아직 없다. 클라이언트가 보낸 문자열을 그대로 기록하면 감사 기록이 거짓이 된다.
LOCAL_ACTOR_ID = "local-owner"
APPROVAL_TTL_HOURS = 72

APPLY_TASK_TYPE = "approval_apply"
CLEANUP_TASK_TYPE = "workspace_cleanup"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite 는 시간대 없는 값을 돌려준다. 비교 전에 기준을 맞춘다."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class ApprovalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def acquire_project_lock(session: AsyncSession, project_id: str) -> None:
    """같은 프로젝트에 대한 적용을 직렬화한다.

    원본 재검사 → 리허설 → 판정 → ff-only → 상태·감사 기록 전체를 감싸야 한다.
    중간만 잠그면 두 승인이 번갈아 들어와 같은 원본을 각자 기준으로 옮긴다.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"project:{project_id}"}
    )


def _audit(
    session: AsyncSession, *, action: str, approval: Approval, detail: dict[str, Any], actor_type: str
) -> None:
    from ..models import AuditLog

    session.add(AuditLog(
        actor_type=actor_type,
        actor_id=LOCAL_ACTOR_ID if actor_type == "user" else None,
        action=action,
        resource_type="approval",
        resource_id=approval.id,
        detail=detail,
        correlation_id=approval.correlation_id,
    ))


async def create_for_task(session: AsyncSession, task: QueueTask, result: dict) -> Approval | None:
    """개발 작업이 완료되는 트랜잭션 안에서 승인을 만든다.

    작업만 completed 로 바뀌고 승인이 없는 상태가 생기면, 사용자는 결과를 보고도
    반영할 방법이 없고 워커는 다시 만들지 않는다.
    """
    if result.get("kind") != "changes" or not result.get("requires_approval"):
        return None
    if task.project_id is None:
        raise ApprovalError("PROJECT_NOT_BOUND", "프로젝트에 묶이지 않은 작업입니다.")

    required = ("result_sha", "result_tree", "base_sha", "branch", "impact_hash", "impact_scope")
    missing = [key for key in required if not result.get(key)]
    if missing:
        raise ApprovalError("APPROVAL_RESULT_INCOMPLETE", f"승인에 필요한 값이 없습니다: {missing}")

    project = await session.get(Project, task.project_id)
    if project is None:
        raise ApprovalError("PROJECT_NOT_BOUND", "작업에 묶인 프로젝트를 찾을 수 없습니다.")

    revision = (await session.scalar(
        select(func.coalesce(func.max(Approval.revision), 0)).where(
            Approval.task_id == task.id, Approval.result_sha == result["result_sha"]
        )
    )) + 1
    sensitive = sorted(result.get("sensitive_files") or [])

    approval = Approval(
        id=str(uuid4()),
        project_id=project.id,
        task_id=task.id,
        thread_id=task.id,
        branch=result["branch"],
        base_sha=result["base_sha"],
        result_sha=result["result_sha"],
        result_tree=result["result_tree"],
        impact_hash=result["impact_hash"],
        impact_scope=result["impact_scope"],
        impact_scope_hash=impact_scope_hash(result["impact_scope"]),
        revision=revision,
        verification_policy_hash=project.verification_policy_hash,
        status="pending",
        has_sensitive_changes=bool(sensitive),
        sensitive_files=sensitive,
        patch_path=result.get("patch_path"),
        patch_sha256=result.get("patch_sha256"),
        expires_at=utc_now() + timedelta(hours=APPROVAL_TTL_HOURS),
        correlation_id=task.correlation_id,
    )
    session.add(approval)
    _audit(
        session,
        action="approval.created",
        approval=approval,
        actor_type="worker",
        detail={"task_id": task.id, "result_sha": approval.result_sha, "revision": revision},
    )
    return approval


async def _load_active(session: AsyncSession, approval_id: str) -> Approval:
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", "승인 요청을 찾을 수 없습니다.")
    return approval


def _check_target(approval: Approval, result_sha: str, revision: int) -> None:
    """승인 ID 만으로 처리하지 않는다.

    화면이 오래된 상태를 들고 있을 수 있으므로 무엇을 승인하는지 함께 받는다.
    """
    if approval.result_sha != result_sha or approval.revision != revision:
        raise ApprovalError(
            "APPROVAL_TARGET_MISMATCH", "승인하려는 결과가 화면에 보이던 것과 다릅니다."
        )


async def _guard_still_valid(session: AsyncSession, approval: Approval) -> Project:
    expires_at = as_utc(approval.expires_at)
    if expires_at is not None and expires_at <= utc_now():
        await _invalidate(session, approval, "expired", "APPROVAL_EXPIRED")
        raise ApprovalError("APPROVAL_EXPIRED", "승인 기한이 지났습니다. 다시 요청하세요.")

    project = await session.get(Project, approval.project_id)
    if project is None:
        raise ApprovalError("PROJECT_NOT_BOUND", "프로젝트를 찾을 수 없습니다.")
    if project.status != "active":
        raise ApprovalError("PROJECT_NOT_ACTIVE", "중지된 프로젝트입니다.")
    if project.verification_policy_hash != approval.verification_policy_hash:
        await _invalidate(session, approval, "invalidated", "VERIFICATION_POLICY_CHANGED")
        raise ApprovalError(
            "VERIFICATION_POLICY_CHANGED",
            "검증 정책이 바뀌어 기존 승인은 무효입니다. 다시 요청하세요.",
        )
    current_scope = impact_scope_hash({
        "version": approval.impact_scope.get("version", 1),
        "project_path": project.project_path,
        "dependency_paths": sorted(project.dependency_paths or []),
    })
    if current_scope != approval.impact_scope_hash:
        await _invalidate(session, approval, "invalidated", "IMPACT_SCOPE_CHANGED")
        raise ApprovalError(
            "IMPACT_SCOPE_CHANGED",
            "영향 범위 설정이 바뀌어 기존 승인은 무효입니다. 다시 요청하세요.",
        )
    return project


async def _invalidate(
    session: AsyncSession, approval: Approval, status: str, reason: str, *, commit: bool = True
) -> None:
    changed = await session.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status.in_(ACTIVE_APPROVAL_STATUSES))
        .values(status=status, invalidated_reason=reason, updated_at=utc_now())
    )
    if changed.rowcount:
        _audit(
            session, action="approval.invalidated", approval=approval,
            actor_type="system", detail={"reason": reason},
        )
    if commit:
        await session.commit()
    else:
        await session.flush()


async def approve(
    session: AsyncSession,
    approval_id: str,
    *,
    result_sha: str,
    revision: int,
    resolve_target: Callable[[Project], tuple[str, str]],
    acknowledge_sensitive: bool = False,
) -> Approval:
    """승인 상태 전환과 적용 작업 등록을 한 트랜잭션으로 처리한다.

    둘이 갈라지면 '승인은 됐는데 아무 일도 안 일어남' 또는
    '승인 안 됐는데 적용 작업이 도는' 상태가 생긴다.

    승인 대상 브랜치와 HEAD 는 요청값이 아니라 서버가 Git 에서 직접 읽는다.
    클라이언트가 정하게 두면 잘못된 브랜치가 승인 대상이 될 수 있다.
    """
    approval = await _load_active(session, approval_id)
    _check_target(approval, result_sha, revision)
    if approval.status != "pending":
        raise ApprovalError("APPROVAL_NOT_PENDING", "이미 처리된 승인입니다.")
    if approval.has_sensitive_changes and not acknowledge_sensitive:
        raise ApprovalError(
            "SENSITIVE_CONFIRMATION_REQUIRED",
            "검증 명령이나 실행 경로를 바꿀 수 있는 파일이 있어 별도 확인이 필요합니다.",
        )
    project = await _guard_still_valid(session, approval)
    target_branch, target_sha = resolve_target(project)
    if not target_branch or not target_sha:
        raise ApprovalError(
            "TARGET_UNREADABLE", "원본의 현재 브랜치를 읽지 못했습니다."
        )

    now = utc_now()
    changed = await session.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.status == "pending",
            Approval.result_sha == result_sha,
            Approval.revision == revision,
        )
        .values(
            status="approved",
            approved_at=now,
            actor_id=LOCAL_ACTOR_ID,
            target_branch=target_branch,
            target_sha_at_approval=target_sha,
            updated_at=now,
        )
    )
    if not changed.rowcount:
        await session.rollback()
        raise ApprovalError("APPROVAL_NOT_PENDING", "이미 처리된 승인입니다.")

    await _queue_apply(session, approval)
    _audit(
        session, action="approval.approved", approval=approval, actor_type="user",
        detail={
            "result_sha": result_sha, "revision": revision,
            "target_branch": target_branch, "target_sha": target_sha,
        },
    )
    await EventLogService.lock_event_sequence(session)
    await session.commit()
    await session.refresh(approval)
    return approval


async def _queue_apply(session: AsyncSession, approval: Approval) -> None:
    """적용 작업 등록은 한곳에서만 한다.

    멱등성 키에 시도 횟수를 넣어, 첫 승인은 중복 등록이 막히고
    사용자가 원본을 정리한 뒤 누르는 재시도는 새 작업으로 들어간다.
    """
    task = await session.get(QueueTask, approval.task_id)
    session.add(QueueTask(
        employee_id=task.employee_id,
        project_id=approval.project_id,
        task_type=APPLY_TASK_TYPE,
        title=f"승인 적용 · {task.title}"[:200],
        input_payload={"approval_id": approval.id},
        idempotency_key=f"{APPLY_TASK_TYPE}:{approval.id}:{approval.revision}:{approval.apply_attempt}",
        request_hash=approval.impact_hash,
        # 원본이 움직인 경우의 자동 재통합은 1회까지만. 그 뒤는 사용자가 판단한다.
        max_attempts=2,
        correlation_id=approval.correlation_id or task.correlation_id,
    ))


async def block_apply(
    session: AsyncSession, approval: Approval, reason: str, *, commit: bool = True
) -> Approval:
    """원본 상태 때문에 적용을 못 한 경우.

    자동으로 몇 초 안에 반복하면 사용자가 파일을 정리할 틈이 없다.
    멈춰 세우고 이유를 남긴 뒤 사용자가 다시 누르게 한다.
    """
    now = utc_now()
    changed = await session.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status.in_(("applying", "approved")))
        .values(status="blocked", last_apply_error=reason[:400], updated_at=now)
    )
    if changed.rowcount:
        _audit(
            session, action="approval.blocked", approval=approval,
            actor_type="system", detail={"reason": reason},
        )
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(approval)
    return approval


async def retry_apply(
    session: AsyncSession, approval_id: str, *, result_sha: str, revision: int
) -> Approval:
    """사용자가 원본을 정리한 뒤 다시 시도한다."""
    approval = await _load_active(session, approval_id)
    _check_target(approval, result_sha, revision)
    if approval.status != "blocked":
        raise ApprovalError("APPROVAL_NOT_BLOCKED", "다시 시도할 수 있는 상태가 아닙니다.")
    await _guard_still_valid(session, approval)

    now = utc_now()
    changed = await session.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status == "blocked")
        .values(
            status="approved",
            apply_attempt=Approval.apply_attempt + 1,
            last_apply_error=None,
            updated_at=now,
        )
    )
    if not changed.rowcount:
        await session.rollback()
        raise ApprovalError("APPROVAL_NOT_BLOCKED", "다시 시도할 수 있는 상태가 아닙니다.")
    await session.refresh(approval)
    await _queue_apply(session, approval)
    _audit(
        session, action="approval.retry_requested", approval=approval,
        actor_type="user", detail={"apply_attempt": approval.apply_attempt},
    )
    await EventLogService.lock_event_sequence(session)
    await session.commit()
    await session.refresh(approval)
    return approval


async def reject(
    session: AsyncSession, approval_id: str, *, result_sha: str, revision: int
) -> Approval:
    """거절은 큐를 기다리지 않는다. 상태만 바로 바꾸고 정리 작업만 큐에 넣는다."""
    approval = await _load_active(session, approval_id)
    _check_target(approval, result_sha, revision)

    now = utc_now()
    changed = await session.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status == "pending")
        .values(status="rejected", actor_id=LOCAL_ACTOR_ID, updated_at=now)
    )
    if not changed.rowcount:
        await session.rollback()
        raise ApprovalError("APPROVAL_NOT_PENDING", "이미 처리된 승인입니다.")

    task = await session.get(QueueTask, approval.task_id)
    session.add(QueueTask(
        employee_id=task.employee_id,
        project_id=approval.project_id,
        task_type=CLEANUP_TASK_TYPE,
        title=f"작업공간 정리 · {task.title}"[:200],
        input_payload={"approval_id": approval.id, "branch": approval.branch},
        idempotency_key=f"{CLEANUP_TASK_TYPE}:{approval.id}:{revision}",
        request_hash=approval.result_sha,
        correlation_id=approval.correlation_id or task.correlation_id,
    ))
    _audit(
        session, action="approval.rejected", approval=approval, actor_type="user",
        detail={"result_sha": result_sha, "revision": revision},
    )
    await EventLogService.lock_event_sequence(session)
    await session.commit()
    await session.refresh(approval)
    return approval


async def begin_apply(session: AsyncSession, approval_id: str, *, commit: bool = True) -> Approval:
    """approved → applying. 이미 applying 이면 재시도로 보고 그대로 진행한다."""
    approval = await _load_active(session, approval_id)
    if approval.status == "applied":
        return approval
    if approval.status not in ("approved", "applying"):
        raise ApprovalError("APPROVAL_NOT_APPROVED", "승인 상태가 아닙니다.")
    if approval.status == "approved":
        await session.execute(
            update(Approval)
            .where(Approval.id == approval.id, Approval.status == "approved")
            .values(status="applying", updated_at=utc_now())
        )
        if commit:
            await session.commit()
            await session.refresh(approval)
        else:
            await session.flush()
            await session.refresh(approval)
    return approval


async def mark_applied(
    session: AsyncSession, approval: Approval, applied_sha: str, *, commit: bool = True
) -> Approval:
    """멱등하게 처리한다. ff-only 는 성공했는데 기록 전에 죽어도 재시도로 복구된다."""
    now = utc_now()
    changed = await session.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status.in_(("applying", "approved")))
        .values(status="applied", applied_at=now, applied_sha=applied_sha, updated_at=now)
    )
    if changed.rowcount:
        _audit(
            session, action="approval.applied", approval=approval, actor_type="system",
            detail={"applied_sha": applied_sha, "result_sha": approval.result_sha},
        )
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(approval)
    return approval


async def invalidate(
    session: AsyncSession, approval: Approval, reason: str, *, commit: bool = True
) -> None:
    await _invalidate(session, approval, "invalidated", reason, commit=commit)
