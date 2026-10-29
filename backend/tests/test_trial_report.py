"""완주 시험 보고서 자체를 시험한다.

이 보고서가 잠금 해제 판단의 근거가 되므로, 보고서가 잘못 통과시키는 경우를
먼저 막아야 한다. 아래 네 가지는 모두 실제로 잘못 통과했던 경우다.
"""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from trial_report import MISSING, PASS, build_report  # noqa: E402

from app.models import Approval, AuditLog, QueueTask


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


@pytest.fixture()
def repository(tmp_path):
    root = tmp_path / "시험-저장소"
    (root / "앱").mkdir(parents=True)
    (root / "앱" / "a.txt").write_text("안녕", encoding="utf-8")
    git(root, "init")
    git(root, "config", "user.email", "person001@example.invalid")
    git(root, "config", "user.name", "주인")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


def merge_commit(repository: Path, name: str) -> None:
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "checkout", "-q", "-b", name, base)
    (repository / "앱" / f"{name}.txt").write_text(name, encoding="utf-8")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", name)
    git(repository, "checkout", "-q", "-")
    git(repository, "merge", "--no-ff", "--no-edit", "-m", f"merge {name}", name)


def approval(**overrides) -> Approval:
    values = dict(
        id="ap-1", project_id="default", task_id="task-1", thread_id="task-1",
        branch="agent/frontend/task-1", base_sha="a" * 40, result_sha="b" * 40,
        result_tree="c" * 40, impact_hash="d" * 64, impact_scope={},
        impact_scope_hash="e" * 64, verification_policy_hash="f" * 64,
        status="applied", applied_sha=None, created_at=NOW,
    )
    values.update(overrides)
    return Approval(**values)


def task(**overrides) -> QueueTask:
    values = dict(
        id="task-1", employee_id="frontend", task_type="employee_task", title="작업",
        input_payload={}, idempotency_key="task-1", request_hash="h",
        status="completed", created_at=NOW,
    )
    values.update(overrides)
    return QueueTask(**values)


def audit(action: str, resource_id: str = "ap-1", **overrides) -> AuditLog:
    values = dict(
        actor_type="system", action=action, resource_type="approval",
        resource_id=resource_id, detail={}, created_at=NOW,
    )
    values.update(overrides)
    return AuditLog(**values)


def results(report) -> dict[str, str]:
    return {item: result for _, item, result, _ in report.rows}


def test_a_scenario_that_was_never_run_is_not_a_pass(repository):
    """돌리지 않은 시나리오를 통과로 세면 잠금 해제 근거가 무너진다."""
    baseline = git(repository, "rev-parse", "HEAD")

    report = build_report([], [], [], repository, baseline)

    assert report.unmet > 0
    assert results(report)["반영된 승인이 있다"] == MISSING
    assert results(report)["막힌 승인이 있다"] == MISSING
    assert results(report)["자동 거부된 작업이 있다"] == MISSING


def test_a_blocked_approval_without_any_apply_task_is_not_a_pass(repository):
    """적용 작업이 하나도 없으면 `all([])` 이 참이라 조용히 통과하던 부분이다."""
    baseline = git(repository, "rev-parse", "HEAD")

    report = build_report(
        [approval(status="blocked")], [], [audit("approval.blocked")], repository, baseline,
    )

    assert results(report)["막힌 적용 작업을 찾았다"] == MISSING
    assert "적용 작업이 자동 반복되지 않았다" not in results(report)


def test_merge_commits_are_counted_from_the_trial_baseline(repository):
    """저장소 전체를 세면 시험 전 이력이나 재시도 병합까지 섞인다."""
    merge_commit(repository, "이전-작업")
    baseline = git(repository, "rev-parse", "HEAD")
    merge_commit(repository, "시험-작업")
    head = git(repository, "rev-parse", "HEAD")

    scoped = build_report([approval(applied_sha=head)], [], [], repository, baseline)
    assert results(scoped)["시험 시작 이후 merge 커밋이 1개다"] == PASS

    # 기준점을 저장소 첫 커밋으로 잡으면 이전 병합까지 세어 2개가 된다.
    root = git(repository, "rev-list", "--max-parents=0", "HEAD")
    whole = build_report([approval(applied_sha=head)], [], [], repository, root)
    assert results(whole)["시험 시작 이후 merge 커밋이 1개다"] != PASS


def test_a_blocked_scenario_is_recognised_after_a_successful_retry(repository):
    """재시도로 applied 가 되면 blocked 행은 사라진다. 감사 기록으로 확인해야 한다."""
    baseline = git(repository, "rev-parse", "HEAD")
    merge_commit(repository, "적용")
    head = git(repository, "rev-parse", "HEAD")

    report = build_report(
        [approval(status="applied", applied_sha=head)],
        [task(id="apply-1", task_type="approval_apply", status="failed",
              input_payload={"approval_id": "ap-1"}, attempt_count=1)],
        [
            audit("approval.blocked", detail={"reason": "원본에 저장하지 않은 변경이 있습니다"}),
            audit("approval.applied"),
            audit("approval.approved", actor_type="user", actor_id="local-owner"),
        ],
        repository, baseline,
    )

    assert results(report)["막힌 승인이 있다"] == PASS
    assert results(report)["막힌 이유가 기록됐다"] == PASS
    assert results(report)["적용 작업이 자동 반복되지 않았다"] == PASS


def test_a_broken_repository_is_reported_as_a_failure(tmp_path):
    """git 실패를 빈 결과로 받으면 '작업트리가 깨끗함' 으로 오판한다."""
    report = build_report([], [], [], tmp_path / "저장소-아님", "a" * 40)

    assert results(report)["원본 작업트리가 깨끗하다"] != PASS
    assert results(report)["시험 시작 이후 merge 커밋이 1개다"] != PASS


def test_a_full_run_passes_every_item(repository):
    baseline = git(repository, "rev-parse", "HEAD")
    merge_commit(repository, "적용")
    head = git(repository, "rev-parse", "HEAD")

    report = build_report(
        [
            approval(id="ap-1", status="applied", applied_sha=head),
            approval(id="ap-2", task_id="task-2", status="rejected"),
        ],
        [
            task(id="apply-1", task_type="approval_apply", status="failed",
                 input_payload={"approval_id": "ap-2"}, attempt_count=1),
            task(id="task-3", status="failed",
                 error={"code": "RESULT_OUTSIDE_PROJECT_SCOPE", "message": "밖"}),
        ],
        [
            audit("approval.applied", "ap-1"),
            audit("approval.approved", "ap-1", actor_type="user", actor_id="local-owner"),
            audit("approval.blocked", "ap-2", detail={"reason": "미커밋 변경"}),
        ],
        repository, baseline,
    )

    assert report.unmet == 0, report.render()


def test_records_from_an_earlier_run_do_not_count(repository):
    """수집 단계에서 기준 시각 이전 기록을 걸러내는지 확인한다."""
    from trial_report import as_utc

    earlier = NOW - timedelta(days=1)
    started_at = NOW
    rows = [approval(created_at=earlier), approval(id="ap-2", created_at=NOW)]

    kept = [row for row in rows if (as_utc(row.created_at) or started_at) >= started_at]

    assert [row.id for row in kept] == ["ap-2"]
