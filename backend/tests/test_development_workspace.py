import gzip
import hashlib
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.codex_managers import CodexRunResult
from app.services.local_employee_profiles import LOCAL_EMPLOYEE_PROFILES
from app.services.task_errors import TaskHandlerError
from app.worker import TaskContext


PROFILE = LOCAL_EMPLOYEE_PROFILES["frontend"]
PROJECT = "AI-company"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return completed.stdout


class WritingRunner:
    """Codex 대신 작업공간에 파일을 쓰는 대역."""

    def __init__(
        self, files: dict[str, str] | None = None, *, commit: bool = False, stage: bool = False
    ) -> None:
        self.files = files or {}
        self.commit = commit
        self.stage = stage
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        cwd = Path(kwargs["cwd"])
        for relative, content in self.files.items():
            target = (cwd / relative).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if self.stage or self.commit:
            git(cwd, "add", "-A")
        if self.commit:
            git(cwd, "commit", "-m", "에이전트가 직접 만든 커밋")
        return CodexRunResult(id="response-1", final_response="작업을 마쳤습니다.")


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "owner@example.com")
    git(repository, "config", "user.name", "저장소 주인")
    (repository / "README.md").write_text("원본", encoding="utf-8")
    (repository / PROJECT).mkdir()
    (repository / PROJECT / "app.txt").write_text("앱", encoding="utf-8")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "initial")
    return repository


def make_runner(tmp_path: Path, repository: Path, codex, **overrides):
    from app.services.development_workspace import DevelopmentWorkspaceRunner

    options = {
        "repository_root": repository,
        "worktree_root": tmp_path / "worktrees",
        "model": "test-model",
        "reasoning_effort": "high",
        "codex_runner": codex,
        "project_path": PROJECT,
        "artifact_root": tmp_path / "artifacts",
    }
    options.update(overrides)
    return DevelopmentWorkspaceRunner(**options)


def make_context(task_id: str, title: str = "화면 수정") -> TaskContext:
    return TaskContext(
        id=task_id, employee_id="frontend", task_type="employee_task", title=title,
        payload={"request": "결과 파일을 만들어 주세요"},
        correlation_id="correlation", attempt_count=1,
    )


def head_of(path: Path) -> str:
    return git(path, "rev-parse", "HEAD").strip()


def staged_in(workspace: Path) -> list[str]:
    return [line for line in git(workspace, "diff", "--cached", "--name-only").splitlines() if line]


# --- 정상 경로 --------------------------------------------------------------

async def test_result_is_committed_with_hashes_and_a_patch_artifact(tmp_path):
    repository = make_repository(tmp_path)
    codex = WritingRunner({"결과.txt": "분리 작업공간 결과", "새 폴더/새 파일.txt": "새 파일"})
    runner = make_runner(tmp_path, repository, codex)

    result = await runner.execute(make_context("task-commit"), PROFILE, "프론트엔드 역할 지침")

    assert (repository / PROJECT / "결과.txt").exists() is False
    assert result["kind"] == "changes"
    assert result["requires_approval"] is True
    assert result["base_sha"] != result["result_sha"]
    assert len(result["result_tree"]) == 40
    assert result["changed_files"] == [
        f"{PROJECT}/결과.txt", f"{PROJECT}/새 폴더/새 파일.txt",
    ]
    # 새로 만든 파일도 diff 에 잡혀야 한다. status 와 diff 가 어긋나던 부분이다.
    assert "새 파일" in result["diff_stat"] and "결과.txt" in result["diff_stat"]

    body = gzip.open(result["patch_path"], "rb").read()
    assert hashlib.sha256(body).hexdigest() == result["patch_sha256"]
    assert result["patch_bytes"] == len(body)
    assert result["patch_preview_truncated"] is False
    assert "분리 작업공간 결과" in body.decode("utf-8")
    assert datetime.fromisoformat(result["artifact_expires_at"]) > datetime.now(timezone.utc)


async def test_full_patch_is_preserved_even_when_the_preview_is_truncated(tmp_path):
    repository = make_repository(tmp_path)
    content = "긴 내용\n" * 400
    codex = WritingRunner({"긴 파일.txt": content})
    runner = make_runner(tmp_path, repository, codex, patch_preview_limit=200)

    result = await runner.execute(make_context("task-large"), PROFILE, "역할")

    assert result["patch_preview_truncated"] is True
    assert "긴 내용" not in result["patch_preview"]      # 미리보기는 요약만
    body = gzip.open(result["patch_path"], "rb").read()
    assert result["patch_bytes"] == len(body) > 200      # 원본은 잘리지 않는다
    assert hashlib.sha256(body).hexdigest() == result["patch_sha256"]
    assert body.decode("utf-8").count("긴 내용") >= 400


async def test_commit_author_is_fixed_and_hooks_do_not_run(tmp_path):
    repository = make_repository(tmp_path)
    hook = repository / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch \"$(git rev-parse --show-toplevel)/hook-ran\"\n", encoding="utf-8")
    hook.chmod(0o755)
    runner = make_runner(tmp_path, repository, WritingRunner({"결과.txt": "결과"}))

    result = await runner.execute(make_context("task-hooks"), PROFILE, "역할")

    workspace = Path(result["workspace"])
    assert (workspace / "hook-ran").exists() is False
    assert git(workspace, "log", "-1", "--format=%an%n%ae%n%cn%n%ce").splitlines() == [
        "AI 운영센터", "ai-company@localhost", "AI 운영센터", "ai-company@localhost",
    ]


async def test_codex_runs_inside_the_registered_project_path(tmp_path):
    repository = make_repository(tmp_path)
    codex = WritingRunner({"src/화면.txt": "화면"})
    runner = make_runner(tmp_path, repository, codex)

    result = await runner.execute(make_context("task-scope"), PROFILE, "역할")

    assert codex.calls[0]["cwd"] == str(Path(result["workspace"]) / PROJECT)
    assert result["changed_files"] == [f"{PROJECT}/src/화면.txt"]
    assert PROJECT in codex.calls[0]["developer_instructions"]


# --- 프로젝트 경로 검증 ------------------------------------------------------

def test_project_path_rejects_escapes_and_repository_scope():
    from app.services.development_workspace import normalize_project_path

    assert normalize_project_path("AI-company") == "AI-company"
    assert normalize_project_path("AI-company\\backend") == "AI-company/backend"
    assert normalize_project_path("AI-company/") == "AI-company"
    assert normalize_project_path(".", allow_repository_scope=True) == "."

    for bad in ("", ".", "..", "../다른-폴더", "AI-company/../..", "/AI-company",
                "/etc/passwd", "C:/Users", "AI-company//backend"):
        with pytest.raises(ValueError):
            normalize_project_path(bad)


def test_runner_refuses_a_repository_wide_scope_by_default(tmp_path):
    repository = make_repository(tmp_path)

    with pytest.raises(ValueError):
        make_runner(tmp_path, repository, WritingRunner(), project_path=".")

    runner = make_runner(
        tmp_path, repository, WritingRunner(), project_path=".", allow_repository_scope=True
    )
    assert runner.project_path == "."


async def test_change_outside_the_project_path_is_rejected_without_committing(tmp_path):
    repository = make_repository(tmp_path)
    codex = WritingRunner({"src/화면.txt": "화면", "../다른-프로그램.txt": "밖"})
    runner = make_runner(tmp_path, repository, codex)

    with pytest.raises(TaskHandlerError) as caught:
        await runner.execute(make_context("task-outside"), PROFILE, "역할")

    assert caught.value.code == "RESULT_OUTSIDE_PROJECT_SCOPE"
    assert caught.value.retryable is False
    workspace = tmp_path / "worktrees" / "frontend" / "task-outside"
    assert head_of(workspace) == head_of(repository)
    assert staged_in(workspace) == []


# --- 비밀정보 차단 -----------------------------------------------------------

def test_credential_files_are_blocked_and_examples_are_not():
    from app.services.development_workspace import is_blocked_path, is_sensitive_path

    for path in ("AI-company/backend/.env", "secrets/server.pem", "keys/id_rsa",
                 "AI-company/.npmrc", "AI-company/.pypirc", "~/.netrc", "certs/api.p8"):
        assert is_blocked_path(path) is True, path
    assert is_blocked_path("AI-company/backend/.env.example") is False
    assert is_sensitive_path("AI-company/package.json") is True
    assert is_sensitive_path(".github/workflows/ci.yml") is True
    assert is_sensitive_path("AI-company/src/App.tsx") is False


async def test_staged_secret_file_is_detected_and_unstaged(tmp_path):
    repository = make_repository(tmp_path)
    codex = WritingRunner({"결과.txt": "결과", ".env": "OPENAI_API_KEY=값"}, stage=True)
    runner = make_runner(tmp_path, repository, codex)

    with pytest.raises(TaskHandlerError) as caught:
        await runner.execute(make_context("task-secret"), PROFILE, "역할")

    assert caught.value.code == "RESULT_SECRET_FILE_BLOCKED"
    workspace = tmp_path / "worktrees" / "frontend" / "task-secret"
    assert staged_in(workspace) == []
    assert head_of(workspace) == head_of(repository)


async def test_credential_inside_file_contents_is_blocked(tmp_path):
    repository = make_repository(tmp_path)
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n-----END RSA PRIVATE KEY-----\n"
    codex = WritingRunner({"설정.txt": key})
    runner = make_runner(tmp_path, repository, codex)

    with pytest.raises(TaskHandlerError) as caught:
        await runner.execute(make_context("task-secret-body"), PROFILE, "역할")

    assert caught.value.code == "RESULT_SECRET_CONTENT_BLOCKED"
    workspace = tmp_path / "worktrees" / "frontend" / "task-secret-body"
    assert staged_in(workspace) == []
    assert head_of(workspace) == head_of(repository)


async def test_generic_credential_shape_is_flagged_not_blocked(tmp_path):
    repository = make_repository(tmp_path)
    codex = WritingRunner({"설정.txt": 'access_token = "aBcDeFgH12345678ijkl"\n'})
    runner = make_runner(tmp_path, repository, codex)

    result = await runner.execute(make_context("task-suspect"), PROFILE, "역할")

    assert result["kind"] == "changes"
    assert f"{PROJECT}/설정.txt" in result["sensitive_files"]


async def test_agent_made_commit_is_detected(tmp_path):
    repository = make_repository(tmp_path)
    runner = make_runner(tmp_path, repository, WritingRunner({"결과.txt": "결과"}, commit=True))

    with pytest.raises(TaskHandlerError) as caught:
        await runner.execute(make_context("task-tampered"), PROFILE, "역할")

    assert caught.value.code == "RESULT_WORKSPACE_TAMPERED"
    assert caught.value.retryable is False


# --- 변경 없음 / 승인 분류 ---------------------------------------------------

async def test_no_change_result_completes_without_approval(tmp_path):
    repository = make_repository(tmp_path)
    runner = make_runner(tmp_path, repository, WritingRunner())

    result = await runner.execute(make_context("task-empty"), PROFILE, "역할")

    assert result["kind"] == "no_changes"
    assert result["requires_approval"] is False
    assert result["result_sha"] is None
    assert result["changed_files"] == []
    assert head_of(Path(result["workspace"])) == head_of(repository)


async def test_sensitive_file_is_committed_but_flagged_for_extra_review(tmp_path):
    repository = make_repository(tmp_path)
    codex = WritingRunner({"package.json": '{"scripts":{"test":"echo"}}', "src/화면.txt": "화면"})
    runner = make_runner(tmp_path, repository, codex)

    result = await runner.execute(make_context("task-sensitive"), PROFILE, "역할")

    assert f"{PROJECT}/package.json" in result["changed_files"]
    assert result["sensitive_files"] == [f"{PROJECT}/package.json"]


async def test_development_task_reuses_same_worktree_on_retry(tmp_path):
    repository = make_repository(tmp_path)
    runner = make_runner(tmp_path, repository, WritingRunner({"결과.txt": "결과"}))
    context = make_context("task-retry", title="재시도")

    first = await runner.execute(context, PROFILE, "역할")
    second = await runner.execute(context, PROFILE, "역할")

    assert first["workspace"] == second["workspace"]
    assert first["branch"] == second["branch"]
    assert first["kind"] == "changes"
    assert second["kind"] == "no_changes"
    assert second["base_sha"] == first["result_sha"]


# --- Git 실행 환경 -----------------------------------------------------------

def test_git_executor_ignores_inherited_git_environment(tmp_path, monkeypatch):
    from app.services.git_executor import GitExecutor

    repository = make_repository(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "없는-저장소" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "없는-작업트리"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "없는-인덱스"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "가로챈 사람")

    executor = GitExecutor(
        hooks_path=tmp_path / "hooks", author_name="AI 운영센터", author_email="ai@localhost"
    )

    assert Path(executor.line(repository, "rev-parse", "--show-toplevel")).resolve() == repository.resolve()
    assert "가로챈 사람" not in executor.line(repository, "log", "-1", "--format=%an")


# --- 아티팩트 정리 -----------------------------------------------------------

def test_expired_artifacts_are_actually_removed(tmp_path):
    from app.services.development_workspace import prune_expired_artifacts

    root = tmp_path / "artifacts"
    old = root / "지난-작업"
    fresh = root / "최근-작업"
    for directory in (old, fresh):
        directory.mkdir(parents=True)
        (directory / "changes.patch.gz").write_bytes(b"x")
    stale = time.time() - 60 * 60 * 24 * 31
    os.utime(old, (stale, stale))

    assert prune_expired_artifacts(root, 30) == 1
    assert old.exists() is False
    assert fresh.exists() is True
    assert prune_expired_artifacts(tmp_path / "없음", 30) == 0


# --- 상태 파싱 --------------------------------------------------------------

def test_porcelain_parsing_keeps_both_sides_of_a_rename():
    from app.services.development_workspace import parse_porcelain_paths

    raw = "R  새 이름.txt\0옛 이름.txt\0?? 새 파일.txt\0 M 고친 파일.txt\0"
    assert parse_porcelain_paths(raw) == ["고친 파일.txt", "새 이름.txt", "새 파일.txt", "옛 이름.txt"]

    # 작업트리 쪽 이름 변경은 두 번째 상태 문자에 온다.
    worktree_rename = " R 새 이름.txt\0옛 이름.txt\0"
    assert parse_porcelain_paths(worktree_rename) == ["새 이름.txt", "옛 이름.txt"]


async def test_unscanned_files_are_reported_instead_of_being_treated_as_safe(tmp_path):
    repository = make_repository(tmp_path)
    codex = WritingRunner({"큰 파일.txt": "가" * 200, "그림.bin": "머리\x00몸통"})
    runner = make_runner(tmp_path, repository, codex, content_scan_byte_limit=100)

    result = await runner.execute(make_context("task-unscanned"), PROFILE, "역할")

    assert result["secret_scan_skipped_files"] == [
        f"{PROJECT}/그림.bin", f"{PROJECT}/큰 파일.txt",
    ]
    # 검사하지 못한 파일은 안전한 파일이 아니라 확인 대상이다.
    for path in result["secret_scan_skipped_files"]:
        assert path in result["sensitive_files"]


async def test_no_change_result_reports_empty_scan_lists(tmp_path):
    repository = make_repository(tmp_path)
    runner = make_runner(tmp_path, repository, WritingRunner())

    result = await runner.execute(make_context("task-empty-scan"), PROFILE, "역할")

    assert result["secret_scan_skipped_files"] == []
    assert result["sensitive_files"] == []
