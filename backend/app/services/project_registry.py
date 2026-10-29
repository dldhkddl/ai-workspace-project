from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Project
from .git_executor import GitExecutor
from .impact_scope import normalize_dependency_paths
from .verification_policy import DEFAULT_VERIFICATION_POLICY, verification_policy_hash


logger = logging.getLogger(__name__)
DEFAULT_PROJECT_ID = "default"


class ProjectRegistrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProjectLocation:
    """작업이 실제로 열릴 위치. 전역 설정이 아니라 작업에 묶인 프로젝트에서 온다."""

    id: str
    repository_root: Path
    project_path: str
    dependency_paths: tuple[str, ...] = ()

    @classmethod
    def from_project(cls, project: Project) -> "ProjectLocation":
        return cls(
            id=project.id,
            repository_root=Path(project.repository_root),
            project_path=project.project_path,
            dependency_paths=tuple(project.dependency_paths or ()),
        )


def _git_toplevel(git: GitExecutor, path: Path) -> Path | None:
    try:
        result = git.run(path, "rev-parse", "--show-toplevel", check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def verify_project_location(
    git: GitExecutor,
    repository_root: Path,
    project_path: str,
    forbidden_names: tuple[str, ...],
) -> Path:
    """등록 전에 실제 위치를 확인한다.

    Git 호출은 반드시 GitExecutor 를 거친다. 직접 subprocess 를 쓰면 GIT_DIR 같은
    외부 환경변수가 살아 있어 엉뚱한 저장소를 가리킬 수 있다.
    """
    repository = repository_root.resolve()
    if not repository.is_dir():
        raise ProjectRegistrationError(
            "PROJECT_NOT_A_GIT_REPOSITORY", f"폴더가 없습니다: {repository}"
        )
    toplevel = _git_toplevel(git, repository)
    if toplevel is None:
        raise ProjectRegistrationError(
            "PROJECT_NOT_A_GIT_REPOSITORY", f"Git 저장소가 아닙니다: {repository}"
        )
    if toplevel != repository:
        raise ProjectRegistrationError(
            "PROJECT_NOT_REPOSITORY_ROOT",
            f"저장소 루트가 아닙니다. 실제 루트는 {toplevel} 입니다.",
        )

    project = (repository if project_path == "." else repository / project_path).resolve()
    if project != repository and repository not in project.parents:
        raise ProjectRegistrationError(
            "PROJECT_OUTSIDE_REPOSITORY", f"프로젝트 경로가 저장소 밖입니다: {project}"
        )
    if not project.is_dir():
        raise ProjectRegistrationError(
            "PROJECT_PATH_MISSING", f"프로젝트 폴더가 없습니다: {project}"
        )

    # Windows 는 대소문자를 구분하지 않으므로 비교도 구분하지 않는다.
    relative_parts = () if project == repository else project.relative_to(repository).parts
    candidates = {part.casefold() for part in relative_parts} | {project.name.casefold()}
    for name in forbidden_names:
        if name.casefold() in candidates:
            raise ProjectRegistrationError(
                "PROJECT_PATH_FORBIDDEN", f"등록할 수 없는 경로입니다: {name}"
            )
    return project


async def _resolve_existing(
    session: AsyncSession, project_id: str, repository_root: str, project_path: str
) -> Project | None:
    by_id = await session.get(Project, project_id)
    if by_id is not None:
        if by_id.repository_root == repository_root and by_id.project_path == project_path:
            return by_id
        raise ProjectRegistrationError(
            "PROJECT_SETTINGS_CHANGED",
            "등록된 프로젝트와 현재 설정의 경로가 다릅니다. "
            f"등록: {by_id.repository_root} / {by_id.project_path}, "
            f"설정: {repository_root} / {project_path}. 자동으로 바꾸지 않으니 직접 확인하세요.",
        )

    by_path = await session.scalar(
        select(Project).where(
            Project.repository_root == repository_root, Project.project_path == project_path
        )
    )
    if by_path is None:
        return None
    if by_path.id == project_id:
        return by_path
    raise ProjectRegistrationError(
        "PROJECT_ID_CONFLICT", f"같은 경로가 이미 '{by_path.id}' 로 등록돼 있습니다."
    )


async def ensure_default_project(
    session: AsyncSession,
    *,
    git: GitExecutor,
    repository_root: Path,
    project_path: str,
    forbidden_names: tuple[str, ...] = (),
    project_id: str = DEFAULT_PROJECT_ID,
    name: str = "기본 프로젝트",
) -> Project:
    """API 와 워커가 함께 쓰는 등록 통로.

    (저장소 경로, 프로젝트 경로) 기준으로 한 번만 만들고, 이미 있으면 그대로 쓴다.
    검증 정책과 의존 경로는 재시작할 때마다 덮어쓰지 않는다. 설정과 DB 가 어긋나면
    조용히 고치지 말고 멈춘다 — 승인이 어느 경로에 걸렸는지 바뀌면 안 되기 때문이다.
    API 와 워커가 동시에 처음 뜨면 둘 다 없다고 보고 넣으므로, 충돌은 실패가 아니라
    '다른 쪽이 먼저 만들었다'로 처리한다.
    """
    verify_project_location(git, repository_root, project_path, forbidden_names)
    root = str(repository_root.resolve())

    existing = await _resolve_existing(session, project_id, root, project_path)
    if existing is not None:
        return existing

    session.add(Project(
        id=project_id,
        name=name,
        repository_root=root,
        project_path=project_path,
        dependency_paths=normalize_dependency_paths(project_path, []),
        verification_policy=dict(DEFAULT_VERIFICATION_POLICY),
        verification_policy_hash=verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
    ))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raced = await _resolve_existing(session, project_id, root, project_path)
        if raced is None:
            raise
        logger.info("기본 프로젝트가 이미 등록돼 있어 그대로 사용합니다: %s", project_id)
        return raced

    created = await _resolve_existing(session, project_id, root, project_path)
    assert created is not None
    return created


def build_git_executor(settings) -> GitExecutor:
    return GitExecutor(
        hooks_path=Path(settings.development_worktree_root) / ".empty-hooks",
        author_name=settings.development_commit_author_name,
        author_email=settings.development_commit_author_email,
    )


async def register_configured_project(session: AsyncSession, settings) -> Project | None:
    """API 와 워커가 시작할 때 함께 부르는 통로.

    설정과 DB 가 어긋난 경우는 개발 모드 잠금 여부와 관계없이 멈춘다. 승인이 어느
    경로에 걸려 있었는지가 바뀌는 상황이라 조용히 넘길 수 없다. 반면 아직 저장소가
    준비되지 않은 정도의 문제는, 개발 작업이 잠겨 있는 동안에는 경고로만 남긴다.
    """
    try:
        return await ensure_default_project(
            session,
            git=build_git_executor(settings),
            repository_root=settings.development_repository_root,
            project_path=settings.development_project_path,
            forbidden_names=settings.forbidden_project_names,
        )
    except ProjectRegistrationError as error:
        if error.code in ("PROJECT_SETTINGS_CHANGED", "PROJECT_ID_CONFLICT"):
            raise
        if settings.development_mode_enabled:
            raise
        logger.warning(
            "기본 프로젝트 등록을 건너뜁니다 (%s): %s", error.code, error.message
        )
        return None
