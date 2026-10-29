import subprocess
from pathlib import Path

import pytest

from app.services.approval_apply import ApplyRequest, ApprovalApplier
from app.services.git_executor import GitExecutor
from app.services.impact_scope import build_impact_scope, build_impact_spec, impact_hash


PROJECT = "AI-company"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "owner@example.com")
    git(repository, "config", "user.name", "저장소 주인")
    write(repository / PROJECT / "app.txt", "앱\n")
    write(repository / "다른-프로그램" / "b.txt", "다른\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "initial")
    branch = git(repository, "rev-parse", "--abbrev-ref", "HEAD").strip()
    return repository, branch


def agent_commit(repository: Path, files: dict[str, str], branch: str = "agent/frontend/task-1") -> str:
    """분리 작업공간에서 만들어진 결과 커밋을 흉내 낸다."""
    base = git(repository, "rev-parse", "HEAD").strip()
    git(repository, "branch", branch, base)
    worktree = repository.parent / f"agent-{branch.replace('/', '-')}"
    git(repository, "worktree", "add", str(worktree), branch)
    for relative, content in files.items():
        write(worktree / relative, content)
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "에이전트 결과")
    result_sha = git(worktree, "rev-parse", "HEAD").strip()
    git(repository, "worktree", "remove", "--force", str(worktree))
    return result_sha


def make_applier(tmp_path: Path, repository: Path) -> ApprovalApplier:
    return ApprovalApplier(
        repository_root=repository,
        integration_root=tmp_path / "integration",
        git=GitExecutor(
            hooks_path=tmp_path / "hooks", author_name="AI 운영센터", author_email="ai@localhost"
        ),
    )


def approved_request(repository: Path, result_sha: str, approval_id: str = "approval-1") -> ApplyRequest:
    """승인 화면에서 사용자가 본 상태를 고정한다."""
    scope = build_impact_scope(PROJECT)

    def resolve(path: str) -> str | None:
        completed = subprocess.run(
            ["git", "rev-parse", f"{result_sha}:{path}"], cwd=repository,
            capture_output=True, text=True, encoding="utf-8",
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    return ApplyRequest(
        approval_id=approval_id,
        target_branch=git(repository, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        result_sha=result_sha,
        scope=scope,
        expected_impact_hash=impact_hash(build_impact_spec(scope, resolve)),
    )


# --- 정상 적용 --------------------------------------------------------------

def test_clean_change_is_rehearsed_then_fast_forwarded(tmp_path):
    repository, branch = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    applier = make_applier(tmp_path, repository)
    request = approved_request(repository, result_sha)

    rehearsal = applier.rehearse(request)
    assert rehearsal.status == "ready"
    assert rehearsal.changed_files == [f"{PROJECT}/화면.txt"]
    # 리허설만으로는 원본이 움직이지 않는다.
    assert git(repository, "rev-parse", "HEAD").strip() == rehearsal.target_sha
    assert (repository / PROJECT / "화면.txt").exists() is False

    outcome = applier.apply(rehearsal)

    assert outcome.status == "applied"
    assert outcome.applied_sha == rehearsal.integration_sha
    assert git(repository, "rev-parse", branch).strip() == outcome.applied_sha
    # ff-only 는 참조뿐 아니라 작업트리도 함께 옮긴다.
    assert (repository / PROJECT / "화면.txt").read_text(encoding="utf-8") == "화면\n"
    assert rehearsal.integration_path.exists() is False


def test_unrelated_target_movement_does_not_force_reapproval(tmp_path):
    """모노레포에서는 무관한 프로젝트의 승인만으로도 target 이 움직인다.

    그것만으로 재승인을 요구하면 사용자는 같은 diff 를 다시 보게 되고,
    몇 번 겪으면 내용을 안 보고 누르기 시작한다.
    """
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    request = approved_request(repository, result_sha)

    write(repository / "다른-프로그램" / "b.txt", "다른 프로그램이 먼저 승인됨\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "무관한 변경")

    rehearsal = make_applier(tmp_path, repository).rehearse(request)

    assert rehearsal.target_sha != git(repository, "rev-parse", f"{result_sha}^").strip()
    assert rehearsal.status == "ready"
    assert rehearsal.impact_hash == request.expected_impact_hash


def test_changed_merge_result_requires_reapproval_and_leaves_the_original_alone(tmp_path):
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    request = approved_request(repository, result_sha)

    write(repository / PROJECT / "설정.txt", "프로젝트 안이 먼저 바뀜\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "프로젝트 경로 변경")
    before = git(repository, "rev-parse", "HEAD").strip()

    applier = make_applier(tmp_path, repository)
    rehearsal = applier.rehearse(request)

    assert rehearsal.status == "needs_reapproval"
    assert rehearsal.impact_hash != request.expected_impact_hash
    assert rehearsal.diff_stat
    assert applier.apply(rehearsal).status == "not_ready"
    assert git(repository, "rev-parse", "HEAD").strip() == before


def test_conflict_stops_before_touching_the_original(tmp_path):
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/app.txt": "에이전트가 고침\n"})
    request = approved_request(repository, result_sha)

    write(repository / PROJECT / "app.txt", "사람이 먼저 고침\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "같은 파일 변경")
    before = git(repository, "rev-parse", "HEAD").strip()

    rehearsal = make_applier(tmp_path, repository).rehearse(request)

    assert rehearsal.status == "conflict"
    assert rehearsal.conflict_files == [f"{PROJECT}/app.txt"]
    assert git(repository, "rev-parse", "HEAD").strip() == before
    assert git(repository, "status", "--porcelain") == ""
    assert (tmp_path / "integration" / "approval-1").exists() is False


# --- 원본 상태 --------------------------------------------------------------

def test_tracked_modification_in_the_original_blocks_the_apply(tmp_path):
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    applier = make_applier(tmp_path, repository)
    rehearsal = applier.rehearse(approved_request(repository, result_sha))
    before = git(repository, "rev-parse", "HEAD").strip()

    write(repository / "다른-프로그램" / "b.txt", "작업 중\n")

    outcome = applier.apply(rehearsal)

    assert outcome.status == "target_dirty"
    assert outcome.blocking_files == ["다른-프로그램/b.txt"]
    assert git(repository, "rev-parse", "HEAD").strip() == before


def test_unrelated_untracked_files_do_not_block_the_apply(tmp_path):
    """실제 개발 폴더에는 추적하지 않는 파일이 늘 있다.

    전부 막으면 승인이 사실상 항상 거부된다.
    """
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    applier = make_applier(tmp_path, repository)
    rehearsal = applier.rehearse(approved_request(repository, result_sha))

    write(repository / "메모.txt", "임시 메모\n")

    assert applier.apply(rehearsal).status == "applied"


def test_untracked_file_on_an_incoming_path_blocks_the_apply(tmp_path):
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    applier = make_applier(tmp_path, repository)
    rehearsal = applier.rehearse(approved_request(repository, result_sha))
    before = git(repository, "rev-parse", "HEAD").strip()

    write(repository / PROJECT / "화면.txt", "덮어쓰면 안 되는 내용\n")

    outcome = applier.apply(rehearsal)

    assert outcome.status == "target_dirty"
    assert outcome.blocking_files == [f"{PROJECT}/화면.txt"]
    assert git(repository, "rev-parse", "HEAD").strip() == before
    assert (repository / PROJECT / "화면.txt").read_text(encoding="utf-8") == "덮어쓰면 안 되는 내용\n"


def test_target_moving_between_rehearsal_and_apply_stops_the_apply(tmp_path):
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    applier = make_applier(tmp_path, repository)
    rehearsal = applier.rehearse(approved_request(repository, result_sha))

    # 승인과 적용 사이에는 큐를 거치므로 시차가 생긴다.
    write(repository / "다른-프로그램" / "b.txt", "그 사이 커밋\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "사이 커밋")
    before = git(repository, "rev-parse", "HEAD").strip()

    outcome = applier.apply(rehearsal)

    assert outcome.status == "target_moved"
    assert git(repository, "rev-parse", "HEAD").strip() == before


def test_detached_original_head_is_refused(tmp_path):
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    applier = make_applier(tmp_path, repository)
    rehearsal = applier.rehearse(approved_request(repository, result_sha))

    git(repository, "checkout", "--detach", "HEAD")

    assert applier.apply(rehearsal).status == "target_moved"


def test_discard_cleans_up_the_integration_worktree(tmp_path):
    repository, _ = make_repository(tmp_path)
    result_sha = agent_commit(repository, {f"{PROJECT}/화면.txt": "화면\n"})
    applier = make_applier(tmp_path, repository)
    rehearsal = applier.rehearse(approved_request(repository, result_sha))
    assert rehearsal.integration_path.exists() is True

    applier.discard(rehearsal)

    assert rehearsal.integration_path.exists() is False
    assert "integration" not in git(repository, "worktree", "list")
