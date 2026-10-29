"""완주 시험 확인 항목을 자동으로 점검한다.

세 시나리오의 확인 항목을 눈으로 세면 빠뜨린다. 시험 DB 와 시험 저장소를 읽어
항목별 통과·실패를 출력한다.

    cd backend
    python scripts/trial_report.py --start     # 시험 시작 기준점 기록
    python scripts/trial_report.py             # 시나리오를 돌릴 때마다 확인

`--start` 로 기준점을 남기지 않으면 과거 기록이 이번 실패를 가릴 수 있으므로
보고서를 만들지 않는다. `DATABASE_URL` 과 `DEVELOPMENT_REPOSITORY_ROOT` 는 시험
환경변수를 그대로 쓴다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Approval, AuditLog, QueueTask


PASS, FAIL, MISSING = "통과", "실패", "미확인"
DEFAULT_STATE = Path(__file__).resolve().parents[1] / ".trial-run.json"


@dataclass(frozen=True)
class GitResult:
    ok: bool
    out: str
    err: str


def git(repository: Path, *args: str) -> GitResult:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *args],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as error:
        return GitResult(False, "", str(error))
    return GitResult(completed.returncode == 0, completed.stdout.strip(), completed.stderr.strip())


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []
        self.unmet = 0

    def check(self, scenario: str, item: str, ok: bool, detail: str = "") -> None:
        self.rows.append((scenario, item, PASS if ok else FAIL, detail))
        if not ok:
            self.unmet += 1

    def missing(self, scenario: str, item: str, reason: str) -> None:
        """돌리지 않은 시나리오는 통과가 아니다. 미확인도 실패로 센다."""
        self.rows.append((scenario, item, MISSING, reason))
        self.unmet += 1

    @staticmethod
    def _width(value: str) -> int:
        # 한글은 터미널에서 두 칸을 차지한다. len() 으로 맞추면 열이 어긋난다.
        return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in value)

    def render(self) -> str:
        lines: list[str] = []
        width = max((self._width(item) for _, item, _, _ in self.rows), default=0)
        current = None
        marks = {PASS: "O", FAIL: "X", MISSING: "?"}
        for scenario, item, result, detail in self.rows:
            if scenario != current:
                lines.append(f"\n[{scenario}]")
                current = scenario
            padding = " " * (width - self._width(item))
            suffix = f" — {detail}" if detail else ""
            lines.append(f"  {marks[result]} {item}{padding}  {result}{suffix}")
        lines.append("")
        if self.unmet:
            lines.append(f"채우지 못한 항목 {self.unmet}건. 개발팀 작업 요청 잠금을 유지하세요.")
        else:
            lines.append("세 시나리오의 확인 항목을 모두 채웠습니다.")
        return "\n".join(lines)


def build_report(
    approvals: list[Approval],
    tasks: list[QueueTask],
    audits: list[AuditLog],
    repository: Path,
    baseline_sha: str,
) -> Report:
    """시험 시작 이후의 기록만 받아서 판정한다."""
    report = Report()
    head = git(repository, "rev-parse", "HEAD")
    status = git(repository, "status", "--porcelain")
    # 저장소 전체가 아니라 시험 시작 이후의 증분만 센다.
    merges = git(repository, "log", "--merges", "--oneline", f"{baseline_sha}..HEAD")

    def audit_ids(action: str) -> set[str]:
        return {row.resource_id for row in audits if row.action == action}

    # --- 1. 정상 작업 -------------------------------------------------------
    applied = [row for row in approvals if row.status == "applied"]
    if not applied:
        report.missing("1. 정상 작업", "반영된 승인이 있다", "아직 돌리지 않았습니다")
    else:
        latest = applied[-1]
        report.check("1. 정상 작업", "반영된 승인이 있다", True, f"{len(applied)}건")
        report.check(
            "1. 정상 작업", "applied_sha 가 현재 HEAD 와 같다",
            head.ok and latest.applied_sha == head.out,
            head.out if head.ok else f"git 실패: {head.err[:40]}",
        )
        report.check(
            "1. 정상 작업", "approval.applied 감사 기록이 1개다",
            len([row for row in audits
                 if row.action == "approval.applied" and row.resource_id == latest.id]) == 1,
        )
        report.check(
            "1. 정상 작업", "승인 감사 기록에 actor 가 남았다",
            any(row.actor_id for row in audits
                if row.action == "approval.approved" and row.resource_id == latest.id),
        )
    report.check(
        "1. 정상 작업", "원본 작업트리가 깨끗하다",
        status.ok and status.out == "",
        (status.out[:60] or "비어 있음") if status.ok else f"git 실패: {status.err[:40]}",
    )
    merge_count = len([line for line in merges.out.splitlines() if line.strip()]) if merges.ok else -1
    report.check(
        "1. 정상 작업", "시험 시작 이후 merge 커밋이 1개다",
        merge_count == 1,
        f"{merge_count}개" if merges.ok else f"git 실패: {merges.err[:40]}",
    )

    # --- 2. 원본 미커밋 변경 -------------------------------------------------
    # 재시도로 applied 가 되면 blocked 행은 사라진다. 감사 기록으로 확인한다.
    blocked_ids = audit_ids("approval.blocked")
    if not blocked_ids:
        report.missing("2. 원본 미커밋 변경", "막힌 승인이 있다", "아직 돌리지 않았습니다")
    else:
        report.check("2. 원본 미커밋 변경", "막힌 승인이 있다", True, f"{len(blocked_ids)}건")
        reasons = [
            row.detail.get("reason") for row in audits
            if row.action == "approval.blocked" and isinstance(row.detail, dict)
        ]
        report.check(
            "2. 원본 미커밋 변경", "막힌 이유가 기록됐다",
            all(isinstance(reason, str) and reason for reason in reasons),
            (reasons[0] or "")[:50] if reasons else "",
        )
        apply_tasks = [
            row for row in tasks
            if row.task_type == "approval_apply"
            and isinstance(row.input_payload, dict)
            and row.input_payload.get("approval_id") in blocked_ids
        ]
        if not apply_tasks:
            report.missing(
                "2. 원본 미커밋 변경", "막힌 적용 작업을 찾았다", "적용 작업 기록이 없습니다",
            )
        else:
            first_attempt = [row for row in apply_tasks if row.attempt_count <= 1]
            report.check(
                "2. 원본 미커밋 변경", "적용 작업이 자동 반복되지 않았다",
                len(first_attempt) == len(apply_tasks),
                ", ".join(f"{row.status}({row.attempt_count}회)" for row in apply_tasks),
            )
            report.check(
                "2. 원본 미커밋 변경", "막힌 적용 작업이 즉시 실패로 끝났다",
                any(row.status == "failed" for row in apply_tasks),
                ", ".join(row.status for row in apply_tasks),
            )

    # --- 3. 프로젝트 경로 밖 변경 -------------------------------------------
    violations = [
        row for row in tasks
        if isinstance(row.error, dict) and row.error.get("code") == "RESULT_OUTSIDE_PROJECT_SCOPE"
    ]
    if not violations:
        report.missing("3. 경로 밖 변경", "자동 거부된 작업이 있다", "아직 돌리지 않았습니다")
    else:
        report.check("3. 경로 밖 변경", "자동 거부된 작업이 있다", True, f"{len(violations)}건")
        report.check(
            "3. 경로 밖 변경", "거부된 작업이 최종 실패다",
            all(row.status == "failed" for row in violations),
            ", ".join(row.status for row in violations),
        )
        violation_ids = {row.id for row in violations}
        leaked = [row for row in approvals if row.task_id in violation_ids]
        report.check("3. 경로 밖 변경", "승인 레코드가 만들어지지 않았다", not leaked, f"{len(leaked)}건")

    # --- 공통 ---------------------------------------------------------------
    stuck = [row for row in approvals if row.status == "applying"]
    report.check("공통", "applying 에 고착된 승인이 없다", not stuck, f"{len(stuck)}건")
    return report


async def collect(session, started_at: datetime):
    def after(rows):
        return [row for row in rows if (as_utc(row.created_at) or started_at) >= started_at]

    approvals = after(list((await session.scalars(
        sa.select(Approval).order_by(Approval.created_at)
    )).all()))
    tasks = after(list((await session.scalars(sa.select(QueueTask))).all()))
    audits = after(list((await session.scalars(sa.select(AuditLog))).all()))
    return approvals, tasks, audits


def start(state_path: Path, repository: Path) -> int:
    head = git(repository, "rev-parse", "HEAD")
    if not head.ok:
        print(f"저장소 HEAD 를 읽지 못했습니다: {head.err}")
        return 2
    state_path.write_text(json.dumps({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "baseline_sha": head.out,
        "repository": str(repository),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"시험 기준점을 기록했습니다: {state_path}")
    print(f"  기준 커밋 {head.out}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="완주 시험 확인 항목 점검")
    parser.add_argument("--start", action="store_true", help="시험 시작 기준점을 기록한다")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="기준점 파일 경로")
    args = parser.parse_args()

    settings = get_settings()
    repository = Path(settings.development_repository_root)
    toplevel = git(repository, "rev-parse", "--show-toplevel")
    if not toplevel.ok:
        print(f"저장소를 읽지 못했습니다: {repository}")
        print("DEVELOPMENT_REPOSITORY_ROOT 를 시험 저장소로 설정했는지 확인하세요.")
        return 2

    if args.start:
        return start(args.state, repository)

    if not args.state.is_file():
        print(f"시험 기준점이 없습니다: {args.state}")
        print("먼저 `python scripts/trial_report.py --start` 를 실행하세요.")
        print("기준점 없이 판정하면 과거 기록이 이번 실패를 가립니다.")
        return 2

    state = json.loads(args.state.read_text(encoding="utf-8"))
    started_at = datetime.fromisoformat(state["started_at"])
    baseline_sha = state["baseline_sha"]

    print(f"저장소   {toplevel.out}")
    print(f"프로젝트 {settings.development_project_path}")
    print(f"DB       {settings.database_url.rsplit('@', 1)[-1]}")
    print(f"기준점   {baseline_sha[:12]} · {started_at.isoformat()}")

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            approvals, tasks, audits = await collect(session, started_at)
    finally:
        await engine.dispose()

    report = build_report(approvals, tasks, audits, repository, baseline_sha)
    print(report.render())
    return 1 if report.unmet else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
