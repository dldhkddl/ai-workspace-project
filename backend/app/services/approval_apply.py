from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .git_executor import GitExecutor
from .impact_scope import build_impact_spec, impact_hash

RehearsalStatus = Literal["ready", "needs_reapproval", "conflict", "failed"]
ApplyStatus = Literal["applied", "target_dirty", "target_moved", "not_ready"]


@dataclass(frozen=True)
class ApplyRequest:
    approval_id: str
    target_branch: str
    result_sha: str
    scope: dict
    expected_impact_hash: str


@dataclass(frozen=True)
class Rehearsal:
    """통합 worktree에서의 리허설 결과. 원본은 아직 손대지 않은 상태다."""

    status: RehearsalStatus
    request: ApplyRequest
    target_sha: str
    integration_path: Path | None = None
    integration_sha: str | None = None
    impact_hash: str | None = None
    changed_files: list[str] = field(default_factory=list)
    diff_stat: str = ""
    conflict_files: list[str] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class ApplyOutcome:
    status: ApplyStatus
    applied_sha: str | None = None
    blocking_files: list[str] = field(default_factory=list)
    message: str = ""


def _parse_status(raw: str) -> list[tuple[str, str]]:
    records = raw.split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(records):
        entry = records[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if path:
            entries.append((code, path))
        if len(code) >= 2 and (code[0] in ("R", "C") or code[1] in ("R", "C")):
            if index < len(records) and records[index]:
                entries.append((code, records[index]))
                index += 1
    return entries


class ApprovalApplier:
    """승인된 변경을 원본에 반영한다.

    원본 작업트리에서 직접 병합하지 않는다. 통합 worktree에서 병합과 판정을 끝내고,
    통과한 정확한 커밋만 원본으로 `--ff-only` 이동한다. 그러면 원본이 충돌 상태로
    남는 일이 구조적으로 생기지 않는다.
    """

    def __init__(self, *, repository_root: Path, integration_root: Path, git: GitExecutor) -> None:
        self.repository_root = repository_root.resolve()
        self.integration_root = integration_root.resolve()
        self.git = git

    # --- 리허설 -----------------------------------------------------------

    def rehearse(self, request: ApplyRequest) -> Rehearsal:
        target_sha = self.git.line(self.repository_root, "rev-parse", request.target_branch)
        workspace = self.integration_root / request.approval_id
        self._remove_worktree(workspace)

        try:
            self.git.run(
                self.repository_root,
                "worktree", "add", "--detach", str(workspace), target_sha,
            )
        except (OSError, subprocess.SubprocessError):
            return Rehearsal(
                status="failed", request=request, target_sha=target_sha,
                message="통합 작업공간을 만들지 못했습니다.",
            )

        merge = self.git.run(
            workspace, "merge", "--no-ff", "--no-edit",
            "-m", f"[AI 운영센터] 승인 적용 {request.approval_id}",
            request.result_sha,
            check=False,
        )
        if merge.returncode != 0:
            conflicts = [
                path for path in
                self.git.output(workspace, "diff", "--name-only", "--diff-filter=U", "-z").split("\0")
                if path
            ]
            self._remove_worktree(workspace)
            return Rehearsal(
                status="conflict", request=request, target_sha=target_sha,
                conflict_files=sorted(conflicts),
                message="원본과 충돌해 적용을 중지했습니다.",
            )

        integration_sha = self.git.line(workspace, "rev-parse", "HEAD")
        current_hash = self._impact_hash(workspace, integration_sha, request.scope)
        changed = [
            path for path in
            self.git.output(workspace, "diff", "--name-only", "-z", f"{target_sha}..{integration_sha}").split("\0")
            if path
        ]
        diff_stat = self.git.output(workspace, "diff", "--stat", f"{target_sha}..{integration_sha}").strip()

        # 판정 기준은 target 이 움직였는지가 아니라 통합 결과가 달라졌는지다.
        # 무관한 경로의 변경만으로 재승인을 요구하면 재승인 버튼이 무의미해진다.
        status: RehearsalStatus = (
            "ready" if current_hash == request.expected_impact_hash else "needs_reapproval"
        )
        return Rehearsal(
            status=status,
            request=request,
            target_sha=target_sha,
            integration_path=workspace,
            integration_sha=integration_sha,
            impact_hash=current_hash,
            changed_files=sorted(changed),
            diff_stat=diff_stat,
            message=(
                "" if status == "ready"
                else "합쳐진 결과가 승인할 때 본 내용과 달라 다시 확인이 필요합니다."
            ),
        )

    # --- 적용 -------------------------------------------------------------

    def apply(self, rehearsal: Rehearsal) -> ApplyOutcome:
        if rehearsal.status != "ready" or rehearsal.integration_sha is None:
            return ApplyOutcome(status="not_ready", message="적용할 수 있는 리허설 결과가 아닙니다.")

        branch = self._checked_out_branch()
        if branch != rehearsal.request.target_branch:
            return ApplyOutcome(
                status="target_moved",
                message="원본이 승인 대상 브랜치에 있지 않아 적용을 중지했습니다.",
            )
        if self.git.line(self.repository_root, "rev-parse", "HEAD") != rehearsal.target_sha:
            return ApplyOutcome(
                status="target_moved",
                message="원본이 리허설 이후 움직여 다시 통합해야 합니다.",
            )

        blocking = self._blocking_paths(rehearsal)
        if blocking:
            return ApplyOutcome(
                status="target_dirty",
                blocking_files=blocking,
                message="원본에 저장하지 않은 변경이 있어 적용을 중지했습니다.",
            )

        try:
            self.git.run(self.repository_root, "merge", "--ff-only", rehearsal.integration_sha)
        except (OSError, subprocess.SubprocessError):
            return ApplyOutcome(status="target_moved", message="원본에 반영하지 못했습니다.")
        applied_sha = self.git.line(self.repository_root, "rev-parse", "HEAD")
        self.discard(rehearsal)
        return ApplyOutcome(status="applied", applied_sha=applied_sha)

    def discard(self, rehearsal: Rehearsal) -> None:
        if rehearsal.integration_path is not None:
            self._remove_worktree(rehearsal.integration_path)

    # --- 보조 -------------------------------------------------------------

    def _impact_hash(self, cwd: Path, sha: str, scope: dict) -> str:
        def resolve(path: str) -> str | None:
            spec = f"{sha}^{{tree}}" if path == "." else f"{sha}:{path}"
            result = self.git.run(cwd, "rev-parse", spec, check=False)
            return result.stdout.strip() if result.returncode == 0 else None

        return impact_hash(build_impact_spec(scope, resolve))

    def _checked_out_branch(self) -> str | None:
        result = self.git.run(
            self.repository_root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _blocking_paths(self, rehearsal: Rehearsal) -> list[str]:
        """적용을 막아야 하는 원본 변경만 고른다.

        추적 중인 파일의 수정은 무조건 막는다. 추적하지 않는 파일은 실제 개발 폴더에
        늘 있기 마련이라, 이번 적용이 건드릴 경로와 겹칠 때만 막는다.
        """
        entries = _parse_status(
            self.git.output(self.repository_root, "status", "--porcelain=v1", "-z", "-uall")
        )
        tracked = sorted({path for code, path in entries if code != "??"})
        if tracked:
            return tracked
        incoming = set(rehearsal.changed_files)
        return sorted({path for code, path in entries if code == "??" and path in incoming})

    def _remove_worktree(self, workspace: Path) -> None:
        self.git.run(self.repository_root, "worktree", "remove", "--force", str(workspace), check=False)
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        self.git.run(self.repository_root, "worktree", "prune", check=False)
