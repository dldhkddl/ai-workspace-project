"""프로젝트 잠금은 PostgreSQL 에서만 실제로 작동한다.

`pg_advisory_xact_lock` 은 SQLite 에서 아무 일도 하지 않으므로, 여기까지의
SQLite 시험은 잠금을 한 번도 검증하지 못했다. 개발팀 잠금을 풀기 전에
이 파일이 실제로 성공해야 한다. skipped 는 통과가 아니다.

    docker compose up -d postgres
    # 운영 DB와 분리된 ai_company_test에 마이그레이션한 뒤 실행한다.
    cd backend && RUN_POSTGRES_TESTS=1 python -m pytest -q tests/test_approval_postgres_concurrency.py
"""
import asyncio
import os
import subprocess
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Approval, AuditLog, Employee, Project, QueueTask, Team
from app.services import approval_service
from app.services.impact_scope import build_impact_scope, build_impact_spec, impact_hash
from app.services.task_errors import TaskHandlerError
from app.services.task_queue import TaskQueueService
from app.services.verification_policy import (
    DEFAULT_VERIFICATION_POLICY,
    verification_policy_hash,
)
from app.worker import TaskContext, build_approval_apply_handler, build_approval_recorder


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="로컬 PostgreSQL 잠금 테스트"
)

DATABASE_URL = os.getenv(
    "POSTGRES_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/ai_company_test?ssl=disable",
)
if make_url(DATABASE_URL).database == "postgres":
    raise RuntimeError("PostgreSQL 동시성 테스트에 운영 DB 'postgres'를 사용할 수 없습니다.")
RUN_ID = str(uuid4())[:8]
TEAM_ID = f"lock-team-{RUN_ID}"
EMPLOYEE_ID = f"lock-employee-{RUN_ID}"
PROJECT_PATH = "AI-company"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def require_migrated(factory) -> None:
    async with factory() as session:
        for table in ("projects", "approvals"):
            if await session.scalar(text(f"SELECT to_regclass('public.{table}')")) is None:
                pytest.fail(
                    f"'{table}' 테이블이 없습니다. 0004 마이그레이션을 먼저 적용하세요: "
                    "cd backend && python -m alembic upgrade head"
                )


async def prepare(factory, repository: Path | None = None) -> None:
    async with factory() as session:
        session.add(Team(id=TEAM_ID, name="잠금 테스트", short_name="잠금", color="#000000"))
        session.add(Employee(
            id=EMPLOYEE_ID, team_id=TEAM_ID, name="잠금 테스트 직원", role="동시성 검증",
            icon="🔒", allowed_tools=[], approval_required=True,
            planned_source="테스트", planned_features=[],
        ))
        if repository is not None:
            session.add(Project(
                id=f"lock-project-{RUN_ID}", name="잠금 테스트 프로젝트",
                repository_root=str(repository), project_path=PROJECT_PATH,
                dependency_paths=[],
                verification_policy=dict(DEFAULT_VERIFICATION_POLICY),
                verification_policy_hash=verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
            ))
        await session.commit()


async def cleanup(factory) -> None:
    async with factory() as session:
        approvals = (await session.scalars(
            sa.select(Approval.id).where(Approval.project_id == f"lock-project-{RUN_ID}")
        )).all()
        if approvals:
            await session.execute(
                delete(AuditLog).where(AuditLog.resource_id.in_(approvals))
            )
        await session.execute(delete(Approval).where(Approval.project_id == f"lock-project-{RUN_ID}"))
        await session.execute(delete(QueueTask).where(QueueTask.employee_id == EMPLOYEE_ID))
        await session.execute(delete(Project).where(Project.id == f"lock-project-{RUN_ID}"))
        await session.execute(delete(Employee).where(Employee.id == EMPLOYEE_ID))
        await session.execute(delete(Team).where(Team.id == TEAM_ID))
        await session.commit()


async def wait_until_advisory_lock(factory, backend_pid: int, pending: asyncio.Task) -> None:
    """상대 세션이 실제로 어드바이저리 잠금에서 대기하는지 확인한다.

    고정 시간 sleep 으로 판단하면 느린 장비에서 거짓 통과가 난다.
    """
    for _ in range(200):
        assert not pending.done(), "잠금을 기다려야 할 요청이 그냥 통과했습니다."
        async with factory() as observer:
            row = (await observer.execute(
                text(
                    "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid = :pid"
                ),
                {"pid": backend_pid},
            )).first()
        if row is not None and row[0] == "Lock" and row[1] == "advisory":
            return
        await asyncio.sleep(0.01)
    pytest.fail("어드바이저리 잠금 대기를 확인하지 못했습니다.")


# --- 1층: 잠금 자체 ---------------------------------------------------------

@pytest.mark.asyncio
async def test_project_lock_blocks_the_same_project_and_not_another():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first_project = f"lock-a-{RUN_ID}"
    second_project = f"lock-b-{RUN_ID}"
    pending: asyncio.Task | None = None
    try:
        async with factory() as holder, factory() as waiter, factory() as other:
            await approval_service.acquire_project_lock(holder, first_project)

            backend_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
            pending = asyncio.create_task(
                approval_service.acquire_project_lock(waiter, first_project)
            )
            await wait_until_advisory_lock(factory, backend_pid, pending)
            assert not pending.done()

            # 다른 프로젝트는 기다리지 않는다.
            await asyncio.wait_for(
                approval_service.acquire_project_lock(other, second_project), timeout=5
            )
            assert not pending.done()
            await other.commit()

            # 잡고 있던 쪽이 끝나면 대기하던 쪽이 이어받는다.
            await holder.commit()
            await asyncio.wait_for(pending, timeout=5)
            assert pending.done()
            await waiter.commit()
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            # 취소를 걸어두고 바로 엔진을 닫으면 대기 중인 연결이 어중간하게 남는다.
            with suppress(asyncio.CancelledError):
                await pending
        await engine.dispose()


# --- 2층: 실제 승인 동시 적용 -----------------------------------------------

@pytest.mark.asyncio
async def test_two_approvals_on_one_project_are_never_applied_against_the_same_head(tmp_path):
    repository = tmp_path / "샘플업체"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "owner@example.com")
    git(repository, "config", "user.name", "주인")
    write(repository / PROJECT_PATH / "app.txt", "앱\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "initial")
    base = git(repository, "rev-parse", "HEAD").strip()

    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        development_worktree_root=tmp_path / "worktrees",
        development_artifact_root=tmp_path / "artifacts",
    )
    project_id = f"lock-project-{RUN_ID}"

    def make_result(task_id: str, filename: str) -> dict:
        branch = f"agent/{EMPLOYEE_ID}/{task_id}"
        git(repository, "branch", branch, base)
        worktree = tmp_path / f"work-{task_id}"
        git(repository, "worktree", "add", str(worktree), branch)
        write(worktree / PROJECT_PATH / filename, f"{filename}\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-m", f"결과 {task_id}")
        result_sha = git(worktree, "rev-parse", "HEAD").strip()
        tree = git(worktree, "rev-parse", f"{result_sha}:{PROJECT_PATH}").strip()
        git(repository, "worktree", "remove", "--force", str(worktree))
        scope = build_impact_scope(PROJECT_PATH)
        return {
            "kind": "changes", "requires_approval": True, "branch": branch,
            "base_sha": base, "result_sha": result_sha, "result_tree": tree,
            "impact_scope": scope,
            "impact_hash": impact_hash(build_impact_spec(
                scope, lambda path: tree if path == PROJECT_PATH else None
            )),
            "sensitive_files": [], "workspace": str(worktree),
        }

    prepared = False
    try:
        await require_migrated(factory)
        await prepare(factory, repository)
        prepared = True

        approvals = []
        for index, filename in enumerate(("가.txt", "나.txt")):
            task_id = f"lock-task-{RUN_ID}-{index}"
            async with factory() as session:
                session.add(QueueTask(
                    id=task_id, employee_id=EMPLOYEE_ID, project_id=project_id,
                    task_type="employee_task", title=f"작업 {index}", input_payload={},
                    idempotency_key=task_id, request_hash="hash",
                    status="running", locked_by="worker-1",
                ))
                await session.commit()
            async with factory() as session:
                await TaskQueueService.complete(
                    session, task_id, "worker-1", make_result(task_id, filename),
                    on_completed=build_approval_recorder(factory),
                )
            async with factory() as session:
                approval = (await session.scalars(
                    sa.select(Approval).where(Approval.task_id == task_id)
                )).one()
                approvals.append(await approval_service.approve(
                    session, approval.id,
                    result_sha=approval.result_sha, revision=approval.revision,
                    resolve_target=lambda project: (
                        git(repository, "rev-parse", "--abbrev-ref", "HEAD").strip(),
                        git(repository, "rev-parse", "HEAD").strip(),
                    ),
                ))

        handler = build_approval_apply_handler(factory, settings)

        def context(approval: Approval) -> TaskContext:
            return TaskContext(
                id=f"apply-{approval.id}", employee_id=EMPLOYEE_ID,
                task_type=approval_service.APPLY_TASK_TYPE, title="승인 적용",
                payload={"approval_id": approval.id}, correlation_id="c",
                attempt_count=1, max_attempts=2, project_id=project_id,
            )

        outcomes = await asyncio.gather(
            handler(context(approvals[0])),
            handler(context(approvals[1])),
            return_exceptions=True,
        )

        applied = [item for item in outcomes if isinstance(item, dict)]
        refused = [item for item in outcomes if isinstance(item, TaskHandlerError)]
        # 둘 다 옛 HEAD 기준으로 동시에 들어가지 않는다.
        assert len(applied) == 1, outcomes
        assert len(refused) == 1, outcomes
        # 두 번째는 첫 번째가 끝난 뒤의 새 HEAD 로 다시 리허설했기 때문에 내용이 달라진다.
        assert refused[0].code == "APPLY_IMPACT_CHANGED"

        head = git(repository, "rev-parse", "HEAD").strip()
        assert head == applied[0]["applied_sha"]
        assert head != base
        # 원본이 충돌 상태로 남지 않는다.
        assert git(repository, "status", "--porcelain") == ""
        assert (repository / ".git" / "MERGE_HEAD").exists() is False
        assert len(git(repository, "log", "--merges", "--oneline").strip().splitlines()) == 1

        async with factory() as session:
            statuses = sorted(
                row.status for row in (await session.scalars(
                    sa.select(Approval).where(Approval.project_id == project_id)
                )).all()
            )
        assert statuses == ["applied", "invalidated"]
    finally:
        # 마이그레이션이 안 된 상태면 cleanup 도 같은 테이블을 찾다가 실패해서,
        # 원래의 안내 메시지가 '테이블 없음' 오류에 덮인다.
        if prepared:
            await cleanup(factory)
        await engine.dispose()
