import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Approval, AuditLog, Base, Project, QueueTask
from app.seed import seed_database
from app.services import approval_service
from app.services.impact_scope import build_impact_scope, impact_scope_hash
from app.services.task_queue import TaskQueueService
from app.services.verification_policy import (
    DEFAULT_VERIFICATION_POLICY,
    verification_policy_hash,
)
from app.worker import build_approval_recorder


PROJECT_PATH = "AI-company"
SCOPE = build_impact_scope(PROJECT_PATH)
RESULT = {
    "kind": "changes",
    "requires_approval": True,
    "branch": "agent/frontend/task-1",
    "base_sha": "a" * 40,
    "result_sha": "b" * 40,
    "result_tree": "c" * 40,
    "impact_hash": "d" * 64,
    "impact_scope": SCOPE,
    "sensitive_files": [],
    "patch_path": "/artifacts/task-1/changes.patch.gz",
    "patch_sha256": "e" * 64,
}


def target(branch: str, sha: str):
    """대상 브랜치·HEAD 는 요청값이 아니라 서버가 읽는다."""
    return lambda project: (branch, sha)


async def make_environment(tmp_path, *, sensitive: bool = False):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approval.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed_database(session)
        session.add(Project(
            id="default", name="기본 프로젝트",
            repository_root="/repo", project_path=PROJECT_PATH,
            dependency_paths=[],
            verification_policy=dict(DEFAULT_VERIFICATION_POLICY),
            verification_policy_hash=verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
        ))
        session.add(QueueTask(
            id="task-1", employee_id="frontend", project_id="default",
            task_type="employee_task", title="화면 수정", input_payload={},
            idempotency_key="task-1", request_hash="hash", status="running",
            locked_by="worker-1",
        ))
        await session.commit()
    return engine, factory


async def complete_task(factory, *, result=None, recorder=None):
    async with factory() as session:
        return await TaskQueueService.complete(
            session, "task-1", "worker-1", result or RESULT,
            on_completed=recorder or build_approval_recorder(factory),
        )


async def only_approval(factory) -> Approval:
    async with factory() as session:
        return (await session.scalars(sa.select(Approval))).one()


# --- 불변식 1: 완료와 승인 생성은 한 트랜잭션 -------------------------------

async def test_completing_a_development_task_creates_its_approval(tmp_path):
    engine, factory = await make_environment(tmp_path)

    assert await complete_task(factory) is True

    approval = await only_approval(factory)
    assert approval.status == "pending"
    assert approval.task_id == "task-1"
    assert approval.project_id == "default"
    assert approval.revision == 1
    assert approval.result_sha == RESULT["result_sha"]
    assert approval.impact_scope_hash == impact_scope_hash(SCOPE)
    assert approval.verification_policy_hash == verification_policy_hash(DEFAULT_VERIFICATION_POLICY)
    assert approval.expires_at is not None
    assert approval.patch_sha256 == RESULT["patch_sha256"]

    async with factory() as session:
        task = await session.get(QueueTask, "task-1")
        assert task.status == "completed"

    await engine.dispose()


async def test_a_failed_approval_rolls_back_the_completion(tmp_path):
    """작업만 완료되고 승인이 없는 상태가 생기면 안 된다."""
    engine, factory = await make_environment(tmp_path)

    async def broken(session, task, result):
        raise approval_service.ApprovalError("BOOM", "승인 생성 실패")

    with pytest.raises(approval_service.ApprovalError):
        await complete_task(factory, recorder=broken)

    async with factory() as session:
        task = await session.get(QueueTask, "task-1")
        assert task.status == "running"
        assert (await session.scalars(sa.select(Approval))).all() == []

    await engine.dispose()


async def test_a_result_without_changes_creates_no_approval(tmp_path):
    engine, factory = await make_environment(tmp_path)
    empty = {"kind": "no_changes", "requires_approval": False}

    assert await complete_task(factory, result=empty) is True

    async with factory() as session:
        assert (await session.scalars(sa.select(Approval))).all() == []
        assert (await session.get(QueueTask, "task-1")).status == "completed"

    await engine.dispose()


# --- 불변식 2: 승인과 적용 작업 등록은 한 트랜잭션 --------------------------

async def test_approving_queues_the_apply_task_in_the_same_transaction(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        approved = await approval_service.approve(
            session, approval.id,
            result_sha=approval.result_sha, revision=approval.revision,
            resolve_target=target("main", "f" * 40),
        )
    assert approved.status == "approved"
    assert approved.actor_id == approval_service.LOCAL_ACTOR_ID
    assert approved.target_branch == "main"
    assert approved.target_sha_at_approval == "f" * 40

    async with factory() as session:
        queued = (await session.scalars(
            sa.select(QueueTask).where(QueueTask.task_type == approval_service.APPLY_TASK_TYPE)
        )).all()
        assert len(queued) == 1
        assert queued[0].input_payload == {"approval_id": approval.id}
        assert queued[0].project_id == "default"

        audit = (await session.scalars(
            sa.select(AuditLog).where(AuditLog.action == "approval.approved")
        )).one()
        assert audit.actor_id == approval_service.LOCAL_ACTOR_ID

    await engine.dispose()


async def test_a_second_approval_neither_changes_state_nor_queues_again(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        await approval_service.approve(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
            resolve_target=target("main", "f" * 40),
        )
    async with factory() as session:
        with pytest.raises(approval_service.ApprovalError) as caught:
            await approval_service.approve(
                session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
                resolve_target=target("main", "9" * 40),
            )
        assert caught.value.code == "APPROVAL_NOT_PENDING"

    async with factory() as session:
        queued = (await session.scalars(
            sa.select(QueueTask).where(QueueTask.task_type == approval_service.APPLY_TASK_TYPE)
        )).all()
        assert len(queued) == 1

    await engine.dispose()


async def test_approving_something_other_than_what_was_shown_is_refused(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        with pytest.raises(approval_service.ApprovalError) as caught:
            await approval_service.approve(
                session, approval.id, result_sha="0" * 40, revision=approval.revision,
                resolve_target=target("main", "f" * 40),
            )
        assert caught.value.code == "APPROVAL_TARGET_MISMATCH"

    assert (await only_approval(factory)).status == "pending"
    await engine.dispose()


# --- 불변식 3: 거절은 즉시, 정리만 큐 ---------------------------------------

async def test_rejecting_is_immediate_and_only_queues_cleanup(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        rejected = await approval_service.reject(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision
        )
    assert rejected.status == "rejected"

    async with factory() as session:
        types = {
            row.task_type for row in
            (await session.scalars(sa.select(QueueTask).where(QueueTask.id != "task-1"))).all()
        }
        assert types == {approval_service.CLEANUP_TASK_TYPE}

    await engine.dispose()


# --- 불변식 5: 민감 파일은 추가 확인 ----------------------------------------

async def test_sensitive_changes_need_an_explicit_confirmation(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(
        factory, result={**RESULT, "sensitive_files": ["AI-company/package.json"]}
    )
    approval = await only_approval(factory)
    assert approval.has_sensitive_changes is True

    async with factory() as session:
        with pytest.raises(approval_service.ApprovalError) as caught:
            await approval_service.approve(
                session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
                resolve_target=target("main", "f" * 40),
            )
        assert caught.value.code == "SENSITIVE_CONFIRMATION_REQUIRED"
    assert (await only_approval(factory)).status == "pending"

    async with factory() as session:
        approved = await approval_service.approve(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
            resolve_target=target("main", "f" * 40), acknowledge_sensitive=True,
        )
    assert approved.status == "approved"
    await engine.dispose()


# --- 설정 변경은 재승인이 아니라 무효 ---------------------------------------

@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("policy", "VERIFICATION_POLICY_CHANGED"),
        ("scope", "IMPACT_SCOPE_CHANGED"),
    ],
)
async def test_configuration_changes_invalidate_instead_of_asking_again(tmp_path, mutation, expected):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        project = await session.get(Project, "default")
        if mutation == "policy":
            project.verification_policy_hash = "9" * 64
        else:
            project.dependency_paths = ["공용"]
        await session.commit()

    async with factory() as session:
        with pytest.raises(approval_service.ApprovalError) as caught:
            await approval_service.approve(
                session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
                resolve_target=target("main", "f" * 40),
            )
        assert caught.value.code == expected

    refreshed = await only_approval(factory)
    assert refreshed.status == "invalidated"
    assert refreshed.invalidated_reason == expected

    async with factory() as session:
        assert (await session.scalars(
            sa.select(QueueTask).where(QueueTask.task_type == approval_service.APPLY_TASK_TYPE)
        )).all() == []

    await engine.dispose()


# --- 불변식 4: applying 은 멱등 ---------------------------------------------

async def test_applying_state_is_idempotent_on_retry(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        await approval_service.approve(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
            resolve_target=target("main", "f" * 40),
        )

    async with factory() as session:
        first = await approval_service.begin_apply(session, approval.id)
        assert first.status == "applying"
    async with factory() as session:
        # 워커가 죽고 다시 집어간 상황
        second = await approval_service.begin_apply(session, approval.id)
        assert second.status == "applying"
        await approval_service.mark_applied(session, second, "1" * 40)
    async with factory() as session:
        third = await approval_service.begin_apply(session, approval.id)
        assert third.status == "applied"
        await approval_service.mark_applied(session, third, "2" * 40)

    final = await only_approval(factory)
    assert final.status == "applied"
    assert final.applied_sha == "1" * 40      # 두 번 기록하지 않는다

    async with factory() as session:
        applied_audits = (await session.scalars(
            sa.select(AuditLog).where(AuditLog.action == "approval.applied")
        )).all()
        assert len(applied_audits) == 1

    await engine.dispose()


async def test_a_second_result_for_the_same_task_gets_the_next_revision(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        await approval_service.reject(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision
        )

    async with factory() as session:
        task = await session.get(QueueTask, "task-1")
        task.status = "running"
        task.locked_by = "worker-1"
        await session.commit()
    await complete_task(factory)

    async with factory() as session:
        revisions = sorted(
            row.revision for row in (await session.scalars(sa.select(Approval))).all()
        )
    assert revisions == [1, 2]
    await engine.dispose()


# --- 원본이 지저분하면 멈추고 사용자가 다시 시도한다 ------------------------

async def test_a_blocked_apply_waits_for_the_user_instead_of_retrying(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        await approval_service.approve(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
            resolve_target=target("main", "f" * 40),
        )
    async with factory() as session:
        applying = await approval_service.begin_apply(session, approval.id)
        await approval_service.block_apply(session, applying, "원본에 저장하지 않은 변경이 있습니다 (a.txt)")

    blocked = await only_approval(factory)
    assert blocked.status == "blocked"
    assert "a.txt" in blocked.last_apply_error
    assert blocked.apply_attempt == 0

    async with factory() as session:
        retried = await approval_service.retry_apply(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision
        )
    assert retried.status == "approved"
    assert retried.apply_attempt == 1
    assert retried.last_apply_error is None

    async with factory() as session:
        queued = (await session.scalars(
            sa.select(QueueTask).where(QueueTask.task_type == approval_service.APPLY_TASK_TYPE)
        )).all()
        # 첫 승인과 재시도가 각각 별도 작업으로 들어간다.
        assert sorted(row.idempotency_key for row in queued) == [
            f"{approval_service.APPLY_TASK_TYPE}:{approval.id}:1:0",
            f"{approval_service.APPLY_TASK_TYPE}:{approval.id}:1:1",
        ]

    await engine.dispose()


async def test_retrying_something_that_is_not_blocked_is_refused(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        with pytest.raises(approval_service.ApprovalError) as caught:
            await approval_service.retry_apply(
                session, approval.id, result_sha=approval.result_sha, revision=approval.revision
            )
        assert caught.value.code == "APPROVAL_NOT_BLOCKED"

    await engine.dispose()


async def test_a_blocked_approval_still_counts_as_live(tmp_path):
    """blocked 는 사용자가 정리하면 이어갈 수 있으므로 살아 있는 승인이다.

    같은 결과에 새 승인이 또 생기면 안 된다.
    """
    from app.models import ACTIVE_APPROVAL_STATUSES

    assert "blocked" in ACTIVE_APPROVAL_STATUSES

    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        await approval_service.approve(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
            resolve_target=target("main", "f" * 40),
        )
    async with factory() as session:
        applying = await approval_service.begin_apply(session, approval.id)
        await approval_service.block_apply(session, applying, "정리 필요")

    async with factory() as session:
        task = await session.get(QueueTask, "task-1")
        task.status = "running"
        task.locked_by = "worker-1"
        await session.commit()

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await complete_task(factory)

    await engine.dispose()


async def test_an_unreadable_target_stops_the_approval(tmp_path):
    engine, factory = await make_environment(tmp_path)
    await complete_task(factory)
    approval = await only_approval(factory)

    async with factory() as session:
        with pytest.raises(approval_service.ApprovalError) as caught:
            await approval_service.approve(
                session, approval.id, result_sha=approval.result_sha, revision=approval.revision,
                resolve_target=lambda project: ("", ""),
            )
        assert caught.value.code == "TARGET_UNREADABLE"

    assert (await only_approval(factory)).status == "pending"
    await engine.dispose()
