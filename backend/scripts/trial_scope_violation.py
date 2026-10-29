"""3번 시나리오(프로젝트 경로 밖 변경)를 확실하게 재현한다.

에이전트에게 "밖도 정리해줘" 라고 부탁해도 순순히 안에만 쓰고 끝낼 수 있어서
시나리오가 재현되지 않는다. 사람이 분리 작업공간을 찾아 손으로 고치는 것도
정확하지 않다. 이 도우미는 Codex 자리에만 대역을 넣고, 결과 확정 경로
(작업공간 상태 검사 → 경로 대조 → 비밀정보 검사 → 시스템 커밋)는 실제 코드를 그대로 탄다.

    cd backend
    python scripts/trial_scope_violation.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Project, QueueTask
from app.services.codex_managers import CodexRunResult
from app.services.development_workspace import DevelopmentWorkspaceRunner
from app.services.local_employee_profiles import LOCAL_EMPLOYEE_PROFILES
from app.services.project_registry import ProjectLocation
from app.services.task_errors import TaskHandlerError
from app.services.task_queue import TaskQueueService
from app.worker import TaskContext, build_development_runner_factory


EMPLOYEE_ID = "frontend"
WORKER_ID = "trial-helper"


class EscapingRunner:
    """프로젝트 안에 하나, 밖에 하나 쓴다."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        cwd = Path(kwargs["cwd"])
        inside = cwd / "시험-정상.txt"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("프로젝트 안", encoding="utf-8")
        outside = (cwd / ".." / "시험-경로위반.txt").resolve()
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("프로젝트 밖", encoding="utf-8")
        return CodexRunResult(id="trial", final_response="작업을 마쳤습니다.")


async def main() -> int:
    settings = get_settings()
    if settings.development_project_path == ".":
        print("프로젝트 경로가 '.' 이라 경로 위반 검사가 꺼져 있습니다.")
        print("시험 저장소에서는 DEVELOPMENT_PROJECT_PATH 를 하위 폴더로 두세요.")
        return 2

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task_id = str(uuid4())
    try:
        async with factory() as session:
            project = await session.scalar(sa.select(Project).where(
                Project.repository_root == str(settings.development_repository_root.resolve()),
                Project.project_path == settings.development_project_path,
            ))
            if project is None:
                print("등록된 프로젝트가 없습니다. API 나 워커를 한 번 띄워 등록하세요.")
                return 2
            session.add(QueueTask(
                id=task_id, employee_id=EMPLOYEE_ID, project_id=project.id,
                task_type="employee_task", title="경로 위반 시험",
                input_payload={"request": "프로젝트 밖까지 고치기"},
                idempotency_key=f"trial-scope-{task_id}", request_hash="trial",
                status="running", locked_by=WORKER_ID, attempt_count=1,
            ))
            await session.commit()
            location = ProjectLocation.from_project(project)

        base = build_development_runner_factory(settings)(location)
        runner = DevelopmentWorkspaceRunner(
            repository_root=base.repository_root,
            worktree_root=base.worktree_root,
            model=base.model,
            reasoning_effort=base.reasoning_effort,
            codex_runner=EscapingRunner(),
            project_path=base.project_path,
            artifact_root=base.artifact_root,
        )
        context = TaskContext(
            id=task_id, employee_id=EMPLOYEE_ID, task_type="employee_task",
            title="경로 위반 시험", payload={"request": "프로젝트 밖까지 고치기"},
            correlation_id=task_id, attempt_count=1, max_attempts=1,
            project_id=project.id, project=location,
        )

        try:
            result = await runner.execute(context, LOCAL_EMPLOYEE_PROFILES[EMPLOYEE_ID], "시험 역할")
        except TaskHandlerError as error:
            async with factory() as session:
                await TaskQueueService.fail(
                    session, task_id, WORKER_ID,
                    {"code": error.code, "message": error.message},
                    retryable=error.retryable,
                )
            print(f"자동 거부됨: {error.code}")
            print(f"  {error.message}")
            if error.code != "RESULT_OUTSIDE_PROJECT_SCOPE":
                print("기대한 코드가 아닙니다. 경로 검사 외의 이유로 막혔습니다.")
                return 1
            print("\n`python scripts/trial_report.py` 로 확인 항목을 점검하세요.")
            return 0

        print("경로 위반이 거부되지 않았습니다. 이건 실패입니다.")
        print(f"  결과: {result.get('kind')} / 변경 {result.get('changed_files')}")
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
