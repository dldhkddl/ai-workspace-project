from __future__ import annotations

import asyncio
import fnmatch
import gzip
import hashlib
import json
import shutil
import subprocess
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from .codex_managers import CodexReasoningEffort, CodexRunResult, CodexRunnerError
from .git_executor import GitExecutor
from .impact_scope import build_impact_scope, build_impact_spec, impact_hash
from .local_employee_profiles import LocalEmployeeProfile
from .task_errors import TaskHandlerError


class WorkspaceCodexRunnerProtocol(Protocol):
    async def run(
        self,
        *,
        prompt: str,
        developer_instructions: str,
        model: str,
        reasoning_effort: CodexReasoningEffort,
        cwd: str,
    ) -> CodexRunResult: ...


class WorkspaceCodexRunner:
    def __init__(self, *, timeout_seconds: float = 1200) -> None:
        self.timeout_seconds = timeout_seconds

    async def run(self, **kwargs: Any) -> CodexRunResult:
        try:
            return await asyncio.wait_for(self._run(**kwargs), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise CodexRunnerError(
                "CODEX_TEMPORARY_FAILURE", "Codex execution timed out", retryable=True
            ) from exc

    async def _run(
        self,
        *,
        prompt: str,
        developer_instructions: str,
        model: str,
        reasoning_effort: CodexReasoningEffort,
        cwd: str,
    ) -> CodexRunResult:
        try:
            from openai_codex import ApprovalMode, AsyncCodex, CodexError, Sandbox, is_retryable_error
        except ImportError as exc:
            raise CodexRunnerError("CODEX_NOT_INSTALLED", str(exc), retryable=False) from exc

        try:
            async with AsyncCodex() as codex:
                account = await codex.account()
                account_root = getattr(account.account, "root", None)
                if getattr(account_root, "type", None) != "chatgpt":
                    raise CodexRunnerError(
                        "CODEX_NOT_AUTHENTICATED",
                        "Codex has no signed-in ChatGPT account",
                        retryable=False,
                    )
                thread = await codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    config={
                        "features": {"browser_use": False},
                        "web_search": "disabled",
                    },
                    cwd=cwd,
                    developer_instructions=developer_instructions,
                    ephemeral=True,
                    model=model,
                    sandbox=Sandbox.workspace_write,
                )
                result = await thread.run(prompt, effort=reasoning_effort)
        except CodexRunnerError:
            raise
        except CodexError as exc:
            retryable = is_retryable_error(exc)
            raise CodexRunnerError(
                "CODEX_TEMPORARY_FAILURE" if retryable else "CODEX_EXECUTION_FAILED",
                str(exc),
                retryable=retryable,
            ) from exc
        except OSError as exc:
            raise CodexRunnerError("CODEX_TEMPORARY_FAILURE", str(exc), retryable=True) from exc

        if not isinstance(result.id, str) or not isinstance(result.final_response, str):
            raise CodexRunnerError("CODEX_INVALID_RESPONSE", "empty response", retryable=True)
        response = result.final_response.strip()
        if not response:
            raise CodexRunnerError("CODEX_INVALID_RESPONSE", "empty response", retryable=True)
        return CodexRunResult(id=result.id, final_response=response)


# 실제 비밀이 들어갈 수 있는 파일. 한 번 커밋하면 브랜치를 지워도 Git 객체에 남으므로
# 기록만 남기지 않고 커밋 전에 차단한다.
BLOCKED_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.p8", "*.jks", "*.keystore",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.ppk", "credentials", "*.crt", "*.cer",
    "*.der", "*.asc", "*.gpg",
    # 레지스트리 토큰이 들어가는 파일이라 분류가 아니라 차단이 맞다.
    ".npmrc", ".pypirc", ".netrc", "_netrc", ".git-credentials",
)
BLOCKED_EXEMPT_NAMES = (".env.example", ".env.sample", ".env.template", ".env.dist")

# 커밋은 하되 승인할 때 별도 확인이 필요한 파일.
# 검증 명령과 실행 경로를 바꿀 수 있어서 결과를 그대로 믿으면 안 된다.
SENSITIVE_FILE_PATTERNS = (
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "pyproject.toml", "poetry.lock", "uv.lock", "requirements*.txt", "setup.py", "setup.cfg",
    "Makefile", "Dockerfile", "docker-compose*.yml", "docker-compose*.yaml",
    "conftest.py", "vite.config.*", "vitest.config.*", "alembic.ini",
    ".gitattributes", ".gitignore", ".gitmodules",
)

# 파일 이름만으로는 못 거른다. 내용에서도 확실한 자격증명 형태는 커밋 전에 막는다.
HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
)
# 오탐이 잦은 형태라 차단하지 않고 별도 확인 대상으로만 올린다.
GENERIC_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{16,}"
)
CONTENT_SCAN_BYTE_LIMIT = 1_000_000
SENSITIVE_DIRECTORIES = frozenset({".github", ".gitlab", ".circleci", ".husky", ".vscode"})


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def is_blocked_path(path: str) -> bool:
    name = _basename(path)
    if name in BLOCKED_EXEMPT_NAMES:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in BLOCKED_FILE_PATTERNS)


def is_sensitive_path(path: str) -> bool:
    parts = path.split("/")
    if SENSITIVE_DIRECTORIES.intersection(parts[:-1]):
        return True
    name = parts[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_FILE_PATTERNS)


def parse_porcelain_paths(raw: str) -> list[str]:
    """`git status --porcelain=v1 -z -uall` 출력에서 변경 경로를 모두 뽑는다.

    이름이 바뀐 항목은 새 경로와 원본 경로가 따로 나오며 둘 다 검사 대상이다.
    NUL 구분을 쓰는 이유는 한글·공백 경로에서 따옴표 처리가 끼어들지 않게 하기 위해서다.
    """
    records = raw.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        entry = records[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if path:
            paths.append(path)
        renamed = len(code) >= 2 and (code[0] in ("R", "C") or code[1] in ("R", "C"))
        if renamed and index < len(records):
            origin = records[index]
            index += 1
            if origin:
                paths.append(origin)
    return sorted(set(paths))


def normalize_project_path(value: str, *, allow_repository_scope: bool = False) -> str:
    """등록된 프로젝트 경로를 검증한다.

    절대경로와 `..` 를 허용하면 경로 검사 자체가 우회되고, `.`(저장소 전체)는
    안전장치를 사실상 끄는 설정이라 별도 위험 옵션을 켠 경우에만 받는다.
    """
    raw = str(value).strip().replace("\\", "/").rstrip("/")
    if raw in ("", "."):
        if allow_repository_scope:
            return "."
        raise ValueError(
            "development_project_path 는 저장소 안의 폴더여야 합니다. "
            "저장소 전체를 대상으로 하려면 development_allow_repository_scope 를 켜세요."
        )
    head = raw.split("/")[0]
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute() or ":" in head:
        raise ValueError("development_project_path 에 절대경로를 쓸 수 없습니다.")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("development_project_path 에 상위 경로 이동을 쓸 수 없습니다.")
    return "/".join(parts)


def prune_expired_artifacts(artifact_root: Path, retention_days: int) -> int:
    """보존 기한이 지난 패치 아티팩트를 실제로 지운다.

    만료일만 기록해 두면 파일은 계속 쌓인다. 워커가 시작할 때 한 번 쓸어낸다.
    """
    if not artifact_root.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for entry in artifact_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            modified = datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                shutil.rmtree(entry)
                removed += 1
        except OSError:
            continue
    return removed


class DevelopmentWorkspaceRunner:
    def __init__(
        self,
        *,
        repository_root: Path,
        worktree_root: Path,
        model: str,
        reasoning_effort: CodexReasoningEffort,
        codex_runner: WorkspaceCodexRunnerProtocol | None = None,
        timeout_seconds: float = 1200,
        project_path: str = "AI-company",
        artifact_root: Path | None = None,
        artifact_retention_days: int = 30,
        patch_preview_limit: int = 200_000,
        allow_repository_scope: bool = False,
        dependency_paths: tuple[str, ...] = (),
        content_scan_byte_limit: int = CONTENT_SCAN_BYTE_LIMIT,
        commit_author_name: str = "AI 운영센터",
        commit_author_email: str = "ai-company@localhost",
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.worktree_root = worktree_root.resolve()
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.codex_runner = codex_runner or WorkspaceCodexRunner(timeout_seconds=timeout_seconds)
        self.project_path = normalize_project_path(
            project_path, allow_repository_scope=allow_repository_scope
        )
        self.artifact_root = (artifact_root or (worktree_root.parent / ".ai-artifacts")).resolve()
        self.artifact_retention_days = artifact_retention_days
        self.patch_preview_limit = patch_preview_limit
        self.content_scan_byte_limit = content_scan_byte_limit
        self.impact_scope = build_impact_scope(self.project_path, list(dependency_paths))
        self.git = GitExecutor(
            hooks_path=self.worktree_root / ".empty-hooks",
            author_name=commit_author_name,
            author_email=commit_author_email,
        )

    @property
    def scope_prefix(self) -> str:
        return "" if self.project_path == "." else f"{self.project_path}/"

    async def execute(self, context, profile: LocalEmployeeProfile, instructions: str) -> dict:
        branch = f"agent/{profile.employee_id}/{context.id}"
        workspace = self.worktree_root / profile.employee_id / context.id
        project_root = workspace if self.project_path == "." else workspace / self.project_path

        try:
            await asyncio.to_thread(self._ensure_worktree, workspace, branch, project_root)
            base_sha = await asyncio.to_thread(self.git.line, workspace, "rev-parse", "HEAD")
        except (OSError, subprocess.SubprocessError) as exc:
            raise TaskHandlerError(
                "WORKSPACE_PREPARATION_FAILED",
                "분리 작업공간을 준비하지 못했습니다.",
                retryable=True,
            ) from exc

        try:
            result = await self.codex_runner.run(
                prompt=self._task_input(context),
                developer_instructions=(
                    f"{instructions}\n\n"
                    "[AI 운영센터 개발 작업 규칙]\n"
                    f"현재 프로젝트 폴더({self.project_path}) 안에서만 작업하세요. "
                    "필요한 파일·셸 도구와 테스트는 사용할 수 있습니다. "
                    "커밋, 병합, 브랜치 변경, 배포, 외부 전송은 하지 마세요. "
                    "비밀정보 파일을 만들거나 읽거나 출력하지 마세요. "
                    "변경 후 관련 테스트를 실행하고 결과를 한국어로 요약하세요."
                ),
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                cwd=str(project_root),
            )
        except CodexRunnerError as exc:
            raise TaskHandlerError(exc.code, str(exc), retryable=exc.retryable) from exc

        if self._contains_sensitive_output(result.final_response, instructions):
            raise TaskHandlerError(
                "SENSITIVE_OUTPUT_BLOCKED",
                "결과에 공개할 수 없는 정보가 포함되어 차단했습니다.",
                retryable=False,
            )

        return await asyncio.to_thread(
            self._finalize, workspace, branch, base_sha, context, profile, result
        )

    # --- 결과 확정 ---------------------------------------------------------

    def _finalize(self, workspace, branch, base_sha, context, profile, result) -> dict:
        try:
            self._verify_workspace(workspace, branch, base_sha)
            changed = parse_porcelain_paths(
                self.git.output(workspace, "status", "--porcelain=v1", "-z", "-uall")
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TaskHandlerError(
                "RESULT_INSPECTION_FAILED",
                "작업 결과를 확인하지 못했습니다.",
                retryable=True,
            ) from exc

        # 프로젝트 폴더만 보지 않고 저장소 전체 상태를 받아 허용 경로와 대조한다.
        prefix = self.scope_prefix
        outside = [path for path in changed if prefix and not path.startswith(prefix)]
        if outside:
            self._discard_staged(workspace)
            raise TaskHandlerError(
                "RESULT_OUTSIDE_PROJECT_SCOPE",
                f"허용된 프로젝트 경로 밖을 변경해 결과를 버렸습니다: {', '.join(outside[:5])}",
                retryable=False,
            )

        blocked = [path for path in changed if is_blocked_path(path)]
        if blocked:
            self._discard_staged(workspace)
            raise TaskHandlerError(
                "RESULT_SECRET_FILE_BLOCKED",
                f"비밀정보가 담길 수 있는 파일을 변경해 커밋하지 않았습니다: {', '.join(blocked[:5])}",
                retryable=False,
            )

        secret_content, suspect_content, unscanned = self._scan_contents(workspace, changed)
        if secret_content:
            self._discard_staged(workspace)
            raise TaskHandlerError(
                "RESULT_SECRET_CONTENT_BLOCKED",
                f"변경 내용에 자격증명으로 보이는 값이 있어 커밋하지 않았습니다: {', '.join(secret_content[:5])}",
                retryable=False,
            )

        common = {
            "response": result.final_response,
            "response_id": result.id,
            "workspace": str(workspace),
            "branch": branch,
            "project_path": self.project_path,
            "base_sha": base_sha,
            "agent": profile.agent_name,
        }

        if not changed:
            return {
                **common,
                "kind": "no_changes",
                "result_sha": None,
                "result_tree": None,
                "impact_scope": dict(self.impact_scope),
                "impact_hash": None,
                "changed_files": [],
                "sensitive_files": [],
                "secret_scan_skipped_files": [],
                "diff_stat": "",
                "requires_approval": False,
            }

        try:
            result_sha = self._system_commit(workspace, context, profile)
            result_tree = self.git.line(
                workspace,
                "rev-parse",
                f"{result_sha}^{{tree}}" if self.project_path == "." else f"{result_sha}:{self.project_path}",
            )
            result_impact = impact_hash(
                build_impact_spec(self.impact_scope, lambda path: self._object_id(workspace, result_sha, path))
            )
            committed = [
                path for path in
                self.git.output(workspace, "diff", "--name-only", "-z", f"{base_sha}..{result_sha}").split("\0")
                if path
            ]
            diff_stat = self.git.output(workspace, "diff", "--stat", f"{base_sha}..{result_sha}").strip()
            patch = self._store_patch(workspace, context.id, base_sha, result_sha)
        except (OSError, subprocess.SubprocessError) as exc:
            raise TaskHandlerError(
                "RESULT_COMMIT_FAILED",
                "작업 결과를 커밋하지 못했습니다.",
                retryable=False,
            ) from exc

        return {
            **common,
            "kind": "changes",
            "result_sha": result_sha,
            "result_tree": result_tree,
            "impact_scope": dict(self.impact_scope),
            "impact_hash": result_impact,
            "changed_files": sorted(committed),
            "sensitive_files": sorted(
                {path for path in committed if is_sensitive_path(path)}
                | set(suspect_content)
                | set(unscanned)
            ),
            "secret_scan_skipped_files": sorted(unscanned),
            "diff_stat": diff_stat,
            "requires_approval": True,
            **patch,
        }

    def _verify_workspace(self, workspace: Path, branch: str, base_sha: str) -> None:
        """에이전트가 스스로 커밋했거나 브랜치를 옮겼는지 확인한다.

        `developer_instructions` 의 '커밋하지 마세요'는 요청이지 통제가 아니므로
        시스템 커밋 전에 실제 상태를 다시 본다.
        """
        head = self.git.run(workspace, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        current = head.stdout.strip() if head.returncode == 0 else None
        if current != branch:
            raise TaskHandlerError(
                "RESULT_WORKSPACE_TAMPERED",
                "작업 브랜치가 바뀌어 결과를 사용할 수 없습니다.",
                retryable=False,
            )
        if self.git.line(workspace, "rev-parse", "HEAD") != base_sha:
            raise TaskHandlerError(
                "RESULT_WORKSPACE_TAMPERED",
                "작업 중 예상하지 못한 커밋이 생겨 결과를 사용할 수 없습니다.",
                retryable=False,
            )

    def _system_commit(self, workspace: Path, context, profile: LocalEmployeeProfile) -> str:
        add_args = ["add", "-A"] if self.project_path == "." else ["add", "-A", "--", self.project_path]
        self.git.run(workspace, *add_args)
        title = " ".join(str(context.title).split())[:120] or "제목 없음"
        self.git.run(
            workspace,
            "commit",
            "--no-verify",
            "-m", f"[AI 운영센터] {profile.employee_id} · {context.id}",
            "-m", title,
        )
        return self.git.line(workspace, "rev-parse", "HEAD")

    def _store_patch(self, workspace: Path, task_id: str, base_sha: str, result_sha: str) -> dict:
        """Git 객체가 정리된 뒤에도 변경 내용을 되살릴 수 있게 원본 패치를 항상 보존한다.

        크기 제한은 화면 미리보기에만 걸고, 원본은 잘라내지 않는다. 잘라낸 것의 해시를
        `patch_sha256` 으로 두면 승인자가 정확한 변경을 다시 확인할 방법이 없어진다.
        원본을 보존하지 못하면 승인 대상으로 넘기지 않고 작업을 실패시킨다.
        """
        patch = self.git.output(workspace, "diff", "--binary", f"{base_sha}..{result_sha}")
        body = patch.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()

        directory = self.artifact_root / task_id
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "changes.patch.gz"
            with gzip.open(path, "wb") as stream:
                stream.write(body)
            with gzip.open(path, "rb") as stream:
                stored = stream.read()
        except OSError as exc:
            raise TaskHandlerError(
                "RESULT_ARTIFACT_FAILED",
                "변경 내용을 보존하지 못해 승인 대상으로 넘기지 않았습니다.",
                retryable=False,
            ) from exc
        if hashlib.sha256(stored).hexdigest() != digest:
            raise TaskHandlerError(
                "RESULT_ARTIFACT_FAILED",
                "보존한 패치가 원본과 달라 승인 대상으로 넘기지 않았습니다.",
                retryable=False,
            )

        oversized = len(body) > self.patch_preview_limit
        preview = (
            self.git.output(workspace, "diff", "--stat", f"{base_sha}..{result_sha}")
            if oversized else patch
        )
        expires_at = datetime.now(timezone.utc) + timedelta(days=self.artifact_retention_days)
        return {
            "patch_path": str(path),
            "patch_sha256": digest,
            "patch_bytes": len(body),
            "patch_preview": preview,
            "patch_preview_truncated": oversized,
            "artifact_expires_at": expires_at.isoformat(),
        }

    def _object_id(self, workspace: Path, sha: str, path: str) -> str | None:
        spec = f"{sha}^{{tree}}" if path == "." else f"{sha}:{path}"
        result = self.git.run(workspace, "rev-parse", spec, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def _discard_staged(self, workspace: Path) -> None:
        """차단된 결과가 인덱스에 남지 않도록 스테이징을 해제한다.

        `git add` 단계에서 이미 만들어진 blob 자체는 gc 전까지 저장소에 남을 수 있다.
        커밋하지 않고 참조를 끊는 것까지가 여기서 할 수 있는 전부다.
        """
        self.git.run(workspace, "reset", "--quiet", check=False)

    def _scan_contents(
        self, workspace: Path, paths: list[str]
    ) -> tuple[list[str], list[str], list[str]]:
        """변경된 파일 내용에서 자격증명 형태를 찾는다.

        확실한 형태(개인키 헤더, AWS·GitHub·Slack 키)는 차단하고,
        오탐이 잦은 일반 대입 형태는 별도 확인 대상으로만 올린다.
        크기·형식 때문에 읽지 못한 파일은 조용히 넘기지 않고 따로 모은다.
        검사하지 못한 것과 안전한 것은 다르다.
        """
        blocked: list[str] = []
        suspect: list[str] = []
        skipped: list[str] = []
        for path in paths:
            target = workspace / path
            try:
                if not target.is_file():
                    continue
                if target.stat().st_size > self.content_scan_byte_limit:
                    skipped.append(path)
                    continue
                raw = target.read_bytes()
            except OSError:
                skipped.append(path)
                continue
            if b"\0" in raw[:8192]:
                skipped.append(path)
                continue
            content = raw.decode("utf-8", errors="replace")
            if any(pattern.search(content) for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS):
                blocked.append(path)
            elif GENERIC_SECRET_PATTERN.search(content):
                suspect.append(path)
        return blocked, suspect, skipped

    # --- 작업공간 준비 -----------------------------------------------------

    def _ensure_worktree(self, workspace: Path, branch: str, project_root: Path) -> None:
        if not (workspace / ".git").exists():
            existing = [path for path in self.worktree_root.glob("*/*") if (path / ".git").exists()]
            if len(existing) >= 50:
                raise OSError("development workspace limit reached")
            workspace.parent.mkdir(parents=True, exist_ok=True)
            self.git.run(
                self.repository_root, "worktree", "add", "-b", branch, str(workspace), "HEAD"
            )
        resolved_workspace = workspace.resolve()
        resolved_project = project_root.resolve()
        if resolved_project != resolved_workspace and resolved_workspace not in resolved_project.parents:
            raise OSError(f"project path escapes the workspace: {self.project_path}")
        if not resolved_project.is_dir():
            raise OSError(f"project path is missing in the workspace: {self.project_path}")

    @staticmethod
    def _contains_sensitive_output(output: str, instructions: str) -> bool:
        normalized_output = " ".join(output.split()).casefold()
        normalized_instructions = " ".join(instructions.split()).casefold()
        if len(normalized_output) >= 80 and any(
            normalized_output[index:index + 80] in normalized_instructions
            for index in range(len(normalized_output) - 79)
        ):
            return True
        secret_patterns = (
            r"\bsk-[a-zA-Z0-9_-]{16,}\b",
            r"(?i)(api[_ -]?key|secret|password|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
        )
        return any(re.search(pattern, output) for pattern in secret_patterns)

    @staticmethod
    def _task_input(context) -> str:
        payload = json.dumps(context.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"작업 제목: {context.title}\n업무 정보(JSON): {payload}"
