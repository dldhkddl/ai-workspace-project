"""실제 Git 저장소 + DB + 워커 핸들러를 함께 돌리는 통합 시험.

조각별 단위 시험은 각각 통과해도 배선이 어긋나면 아무것도 반영되지 않는다.
개발팀 잠금을 풀기 전에 전체 흐름이 실제로 맞물리는지 여기서 확인한다.
"""
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Approval, AuditLog, Base, Project, QueueTask
from app.seed import seed_database
from app.services import approval_service
from app.services.impact_scope import build_impact_scope
from app.services.task_errors import TaskHandlerError
from app.worker import (
    TaskContext,
    build_approval_apply_handler,
    build_approval_recorder,
)


PROJECT = "AI-company"
EMPLOYEE = "frontend"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "샘플업체"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "owner@example.com")
    git(repository, "config", "user.name", "저장소 주인")
    write(repository / PROJECT / "app.txt", "앱\n")
    write(repository / "다른-프로그램" / "b.txt", "다른\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "initial")
    return repository, git(repository, "rev-parse", "--abbrev-ref", "HEAD").strip()


def agent_result(repository: Path, files: dict[str, str], task_id: str) -> dict:
    """개발 실행기가 만들어 낸 결과를 흉내 낸다(시스템 커밋까지 끝난 상태)."""
    branch = f"agent/{EMPLOYEE}/{task_id}"
    base = git(repository, "rev-parse", "HEAD").strip()
    git(repository, "branch", branch, base)
    worktree = repository.parent / f"work-{task_id}"
    git(repository, "worktree", "add", str(worktree), branch)
    for relative, content in files.items():
        write(worktree / relative, content)
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "에이전트 결과")
    result_sha = git(worktree, "rev-parse", "HEAD").strip()
    tree = git(worktree, "rev-parse", f"{result_sha}:{PROJECT}").strip()
    git(repository, "worktree", "remove", "--force", str(worktree))

    scope = build_impact_scope(PROJECT)
    from app.services.impact_scope import build_impact_spec, impact_hash

    return {
        "kind": "changes",
        "requires_approval": True,
        "branch": branch,
        "base_sha": base,
        "result_sha": result_sha,
        "result_tree": tree,
        "impact_scope": scope,
        "impact_hash": impact_hash(build_impact_spec(scope, lambda path: tree if path == PROJECT else None)),
        "sensitive_files": [],
        "workspace": str(worktree),
        "patch_path": None,
        "patch_sha256": None,
    }


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        development_mode_enabled=True,
        development_worktree_root=tmp_path / "worktrees",
        development_artifact_root=tmp_path / "artifacts",
    )


async def make_database(tmp_path: Path, repository: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'flow.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed_database(session)
        from app.services.verification_policy import (
            DEFAULT_VERIFICATION_POLICY,
            verification_policy_hash,
        )

        session.add(Project(
            id="default", name="기본 프로젝트",
            repository_root=str(repository), project_path=PROJECT,
            dependency_paths=[],
            verification_policy=dict(DEFAULT_VERIFICATION_POLICY),
            verification_policy_hash=verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
        ))
        await session.commit()
    return engine, factory


async def queue_task(factory, task_id: str) -> None:
    async with factory() as session:
        session.add(QueueTask(
            id=task_id, employee_id=EMPLOYEE, project_id="default",
            task_type="employee_task", title="화면 수정", input_payload={},
            idempotency_key=task_id, request_hash="hash",
            status="running", locked_by="worker-1",
        ))
        await session.commit()


async def complete_with_approval(factory, task_id: str, result: dict) -> Approval:
    from app.services.task_queue import TaskQueueService

    async with factory() as session:
        await TaskQueueService.complete(
            session, task_id, "worker-1", result, on_completed=build_approval_recorder(factory)
        )
    async with factory() as session:
        return (await session.scalars(
            sa.select(Approval).where(Approval.task_id == task_id)
        )).one()


def target_reader(repository: Path):
    def resolve(project):
        branch = git(repository, "rev-parse", "--abbrev-ref", "HEAD").strip()
        return branch, git(repository, "rev-parse", "HEAD").strip()

    return resolve


async def approve(factory, approval: Approval, repository: Path) -> Approval:
    async with factory() as session:
        return await approval_service.approve(
            session, approval.id,
            result_sha=approval.result_sha, revision=approval.revision,
            resolve_target=target_reader(repository),
        )


def apply_context(approval: Approval, *, attempt: int = 1, max_attempts: int = 2) -> TaskContext:
    return TaskContext(
        id=f"apply-{approval.id}", employee_id=EMPLOYEE,
        task_type=approval_service.APPLY_TASK_TYPE, title="승인 적용",
        payload={"approval_id": approval.id}, correlation_id="c",
        attempt_count=attempt, max_attempts=max_attempts, project_id="default",
    )


async def reload(factory, approval_id: str) -> Approval:
    async with factory() as session:
        return await session.get(Approval, approval_id)


async def audit_count(factory, action: str) -> int:
    async with factory() as session:
        return len((await session.scalars(
            sa.select(AuditLog).where(AuditLog.action == action)
        )).all())


# --- 1. 정상 적용 -----------------------------------------------------------

async def test_the_whole_flow_applies_the_change_and_records_it_once(tmp_path):
    repository, branch = make_repository(tmp_path)
    engine, factory = await make_database(tmp_path, repository)
    await queue_task(factory, "task-1")
    result = agent_result(repository, {f"{PROJECT}/화면.txt": "화면\n"}, "task-1")
    approval = await complete_with_approval(factory, "task-1", result)
    approval = await approve(factory, approval, repository)
    before = git(repository, "rev-parse", "HEAD").strip()

    handler = build_approval_apply_handler(factory, make_settings(tmp_path))
    outcome = await handler(apply_context(approval))

    assert outcome["status"] == "applied"
    after = git(repository, "rev-parse", "HEAD").strip()
    assert after != before
    assert (repository / PROJECT / "화면.txt").read_text(encoding="utf-8") == "화면\n"
    assert git(repository, "status", "--porcelain") == ""
    assert git(repository, "rev-parse", branch).strip() == after

    final = await reload(factory, approval.id)
    assert final.status == "applied"
    assert final.applied_sha == after
    assert await audit_count(factory, "approval.applied") == 1

    await engine.dispose()


# --- 2. dirty 차단 ----------------------------------------------------------

async def test_a_dirty_original_is_left_untouched_and_the_approval_is_blocked(tmp_path):
    repository, _ = make_repository(tmp_path)
    engine, factory = await make_database(tmp_path, repository)
    await queue_task(factory, "task-2")
    result = agent_result(repository, {f"{PROJECT}/화면.txt": "화면\n"}, "task-2")
    approval = await complete_with_approval(factory, "task-2", result)
    approval = await approve(factory, approval, repository)
    before = git(repository, "rev-parse", "HEAD").strip()

    write(repository / "다른-프로그램" / "b.txt", "작업 중\n")

    handler = build_approval_apply_handler(factory, make_settings(tmp_path))
    with pytest.raises(TaskHandlerError) as caught:
        await handler(apply_context(approval))

    assert caught.value.code == "APPLY_TARGET_DIRTY"
    assert caught.value.retryable is False          # 자동 재시도 없음
    assert git(repository, "rev-parse", "HEAD").strip() == before
    assert (repository / "다른-프로그램" / "b.txt").read_text(encoding="utf-8") == "작업 중\n"
    assert (repository / PROJECT / "화면.txt").exists() is False

    blocked = await reload(factory, approval.id)
    assert blocked.status == "blocked"
    assert "다른-프로그램/b.txt" in blocked.last_apply_error
    assert await audit_count(factory, "approval.blocked") == 1

    await engine.dispose()


# --- 3. 경로 위반은 작업 단계에서 자동 거부 ---------------------------------

async def test_a_scope_violation_is_refused_at_the_work_stage(tmp_path):
    """승인 거절이 아니라 결과 확정 단계에서 자동 거부다. 승인은 만들어지지도 않는다."""
    from app.services.codex_managers import CodexRunResult
    from app.services.development_workspace import DevelopmentWorkspaceRunner
    from app.services.local_employee_profiles import LOCAL_EMPLOYEE_PROFILES

    repository, _ = make_repository(tmp_path)
    engine, factory = await make_database(tmp_path, repository)
    before = git(repository, "rev-parse", "HEAD").strip()

    class EscapingRunner:
        async def run(self, **kwargs):
            cwd = Path(kwargs["cwd"])
            write(cwd / "정상.txt", "안")
            write((cwd / ".." / "다른-프로그램" / "몰래.txt").resolve(), "밖")
            return CodexRunResult(id="r", final_response="했습니다")

    runner = DevelopmentWorkspaceRunner(
        repository_root=repository,
        worktree_root=tmp_path / "worktrees",
        model="m", reasoning_effort="high",
        codex_runner=EscapingRunner(),
        project_path=PROJECT,
        artifact_root=tmp_path / "artifacts",
    )
    context = TaskContext(
        id="task-3", employee_id=EMPLOYEE, task_type="employee_task", title="화면 수정",
        payload={"request": "고쳐줘"}, correlation_id="c", attempt_count=1, project_id="default",
    )

    with pytest.raises(TaskHandlerError) as caught:
        await runner.execute(context, LOCAL_EMPLOYEE_PROFILES[EMPLOYEE], "역할")

    assert caught.value.code == "RESULT_OUTSIDE_PROJECT_SCOPE"
    # 원본도 시스템 커밋도 움직이지 않는다.
    assert git(repository, "rev-parse", "HEAD").strip() == before
    workspace = tmp_path / "worktrees" / EMPLOYEE / "task-3"
    assert git(workspace, "rev-parse", "HEAD").strip() == before
    async with factory() as session:
        assert (await session.scalars(sa.select(Approval))).all() == []

    await engine.dispose()


# --- 4. Git 반영 후 DB 기록 실패 --------------------------------------------

async def test_a_crash_after_the_merge_recovers_without_merging_twice(tmp_path, monkeypatch):
    """ff-only 는 끝났는데 기록 전에 죽은 경우.

    재시도는 조상 관계를 믿지 않고 다시 리허설한다. 이미 반영돼 있으면
    병합이 no-op 이 되어 그대로 통과한다.
    """
    repository, _ = make_repository(tmp_path)
    engine, factory = await make_database(tmp_path, repository)
    await queue_task(factory, "task-4")
    result = agent_result(repository, {f"{PROJECT}/화면.txt": "화면\n"}, "task-4")
    approval = await complete_with_approval(factory, "task-4", result)
    approval = await approve(factory, approval, repository)
    handler = build_approval_apply_handler(factory, make_settings(tmp_path))

    real_mark = approval_service.mark_applied
    calls = {"count": 0}

    async def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("DB 기록 실패")
        return await real_mark(*args, **kwargs)

    monkeypatch.setattr(approval_service, "mark_applied", flaky)

    with pytest.raises(RuntimeError):
        await handler(apply_context(approval))

    merged = git(repository, "rev-parse", "HEAD").strip()
    assert (repository / PROJECT / "화면.txt").exists() is True   # Git 은 이미 반영됨
    stuck = await reload(factory, approval.id)
    assert stuck.status == "applying"                              # 기록은 안 됨

    outcome = await handler(apply_context(approval, attempt=2))

    assert outcome["status"] == "applied"
    assert git(repository, "rev-parse", "HEAD").strip() == merged  # 중복 병합 없음
    merges = git(repository, "log", "--merges", "--oneline").strip().splitlines()
    assert len(merges) == 1
    final = await reload(factory, approval.id)
    assert final.status == "applied"
    assert final.applied_sha == merged
    assert await audit_count(factory, "approval.applied") == 1     # 감사도 하나

    await engine.dispose()


# --- 5. target 이동 반복 -----------------------------------------------------

async def test_a_repeatedly_moving_target_ends_blocked_instead_of_stuck(tmp_path, monkeypatch):
    repository, _ = make_repository(tmp_path)
    engine, factory = await make_database(tmp_path, repository)
    await queue_task(factory, "task-5")
    result = agent_result(repository, {f"{PROJECT}/화면.txt": "화면\n"}, "task-5")
    approval = await complete_with_approval(factory, "task-5", result)
    approval = await approve(factory, approval, repository)
    handler = build_approval_apply_handler(factory, make_settings(tmp_path))

    from app.services import approval_apply

    real_apply = approval_apply.ApprovalApplier.apply

    def moved(self, rehearsal):
        # 리허설과 ff-only 사이에 원본이 움직인 상황
        write(repository / "다른-프로그램" / "b.txt", f"사이 커밋 {rehearsal.integration_sha[:7]}\n")
        git(repository, "add", "-A")
        git(repository, "commit", "-m", "사이 커밋")
        return real_apply(self, rehearsal)

    monkeypatch.setattr(approval_apply.ApprovalApplier, "apply", moved)

    with pytest.raises(TaskHandlerError) as first:
        await handler(apply_context(approval, attempt=1, max_attempts=2))
    assert first.value.code == "APPLY_TARGET_MOVED"
    assert first.value.retryable is True                # 1회는 자동 재통합
    assert (await reload(factory, approval.id)).status == "applying"

    with pytest.raises(TaskHandlerError) as second:
        await handler(apply_context(approval, attempt=2, max_attempts=2))
    assert second.value.code == "APPLY_TARGET_MOVED"
    assert second.value.retryable is False              # 소진되면 사용자에게 넘긴다

    blocked = await reload(factory, approval.id)
    assert blocked.status == "blocked"                  # applying 고착 없음
    assert blocked.last_apply_error

    # 사용자가 다시 시도할 수 있다.
    monkeypatch.setattr(approval_apply.ApprovalApplier, "apply", real_apply)
    async with factory() as session:
        retried = await approval_service.retry_apply(
            session, approval.id, result_sha=approval.result_sha, revision=approval.revision
        )
    assert retried.status == "approved"

    await engine.dispose()
