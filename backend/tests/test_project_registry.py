import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base, Project
from app.seed import seed_database
from app.services.git_executor import GitExecutor
from app.services.project_registry import (
    ProjectRegistrationError,
    ensure_default_project,
    register_configured_project,
    verify_project_location,
)
from app.services.verification_policy import DEFAULT_VERIFICATION_POLICY, verification_policy_hash


PROJECT = "AI-company"


def executor(tmp_path: Path) -> GitExecutor:
    return GitExecutor(
        hooks_path=tmp_path / "hooks", author_name="AI 운영센터", author_email="ai@localhost"
    )


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "샘플업체"
    (repository / PROJECT).mkdir(parents=True)
    (repository / "AI-메모리" / ".claude").mkdir(parents=True)
    (repository / PROJECT / "app.txt").write_text("앱", encoding="utf-8")
    git(repository, "init")
    git(repository, "config", "user.email", "owner@example.com")
    git(repository, "config", "user.name", "주인")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "initial")
    return repository


async def make_session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed_database(session)
    return engine, factory


# --- 위치 검사 --------------------------------------------------------------

def test_location_checks_reject_unsafe_registrations(tmp_path):
    repository = make_repository(tmp_path)

    assert verify_project_location(executor(tmp_path), repository, PROJECT, ("AI-메모리",)) == repository / PROJECT

    plain = tmp_path / "저장소-아님"
    plain.mkdir()
    with pytest.raises(ProjectRegistrationError) as caught:
        verify_project_location(executor(tmp_path), plain, ".", ())
    assert caught.value.code == "PROJECT_NOT_A_GIT_REPOSITORY"

    with pytest.raises(ProjectRegistrationError) as caught:
        verify_project_location(executor(tmp_path), repository / PROJECT, ".", ())
    assert caught.value.code == "PROJECT_NOT_REPOSITORY_ROOT"

    with pytest.raises(ProjectRegistrationError) as caught:
        verify_project_location(executor(tmp_path), repository, "없는-폴더", ())
    assert caught.value.code == "PROJECT_PATH_MISSING"


def test_forbidden_paths_cannot_be_registered(tmp_path):
    """에이전트가 자기 지침을 고칠 수 있는 경로는 승인 대상이 될 수 없다."""
    repository = make_repository(tmp_path)

    for path in ("AI-메모리", "AI-메모리/.claude"):
        with pytest.raises(ProjectRegistrationError) as caught:
            verify_project_location(executor(tmp_path), repository, path, ("AI-메모리",))
        assert caught.value.code == "PROJECT_PATH_FORBIDDEN"


# --- 등록 ------------------------------------------------------------------

async def test_project_is_registered_once_and_reused(tmp_path):
    repository = make_repository(tmp_path)
    engine, factory = await make_session(tmp_path)

    async with factory() as session:
        first = await ensure_default_project(
            session, git=executor(tmp_path), repository_root=repository, project_path=PROJECT
        )
        assert first.verification_policy == DEFAULT_VERIFICATION_POLICY
        assert first.verification_policy_hash == verification_policy_hash(DEFAULT_VERIFICATION_POLICY)
        assert first.dependency_paths == []

        # 사용자가 정책과 의존 경로를 바꾼 뒤 재시작하는 상황
        first.verification_policy = {"version": 1, "enabled": False}
        first.dependency_paths = ["공용"]
        first.name = "직접 고친 이름"
        await session.commit()

    async with factory() as session:
        again = await ensure_default_project(
            session, git=executor(tmp_path), repository_root=repository, project_path=PROJECT
        )
        assert again.id == first.id
        assert again.dependency_paths == ["공용"]
        assert again.name == "직접 고친 이름"

    await engine.dispose()


async def test_settings_and_database_mismatch_stops_instead_of_rewriting(tmp_path):
    repository = make_repository(tmp_path)
    other = tmp_path / "다른-저장소"
    (other / PROJECT).mkdir(parents=True)
    git(other, "init")
    git(other, "config", "user.email", "owner@example.com")
    git(other, "config", "user.name", "주인")
    (other / PROJECT / "a.txt").write_text("a", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-m", "initial")

    engine, factory = await make_session(tmp_path)
    async with factory() as session:
        await ensure_default_project(session, git=executor(tmp_path), repository_root=repository, project_path=PROJECT)

    async with factory() as session:
        with pytest.raises(ProjectRegistrationError) as caught:
            await ensure_default_project(session, git=executor(tmp_path), repository_root=other, project_path=PROJECT)
        assert caught.value.code == "PROJECT_SETTINGS_CHANGED"

    async with factory() as session:
        with pytest.raises(ProjectRegistrationError) as caught:
            await ensure_default_project(
                session, git=executor(tmp_path), repository_root=repository,
                project_path=PROJECT, project_id="다른-아이디"
            )
        assert caught.value.code == "PROJECT_ID_CONFLICT"

    await engine.dispose()


async def test_startup_warns_while_locked_but_never_hides_a_mismatch(tmp_path):
    repository = make_repository(tmp_path)
    engine, factory = await make_session(tmp_path)

    locked = Settings(
        _env_file=None,
        development_mode_enabled=False,
        development_repository_root=tmp_path / "없는-저장소",
        development_project_path=PROJECT,
    )
    async with factory() as session:
        # 아직 준비되지 않은 경로는 잠긴 동안 경고로만 넘어간다.
        assert await register_configured_project(session, locked) is None

    unlocked = Settings(
        _env_file=None,
        development_mode_enabled=True,
        development_repository_root=tmp_path / "없는-저장소",
        development_project_path=PROJECT,
    )
    async with factory() as session:
        with pytest.raises(ProjectRegistrationError):
            await register_configured_project(session, unlocked)

    # 설정과 DB 불일치는 잠금 여부와 관계없이 멈춘다.
    async with factory() as session:
        await ensure_default_project(session, git=executor(tmp_path), repository_root=repository, project_path=PROJECT)
    moved = Settings(
        _env_file=None,
        development_mode_enabled=False,
        development_repository_root=repository,
        development_project_path=".",
    )
    async with factory() as session:
        with pytest.raises(ProjectRegistrationError) as caught:
            await register_configured_project(session, moved)
        assert caught.value.code == "PROJECT_SETTINGS_CHANGED"

    await engine.dispose()


def test_forbidden_names_come_from_settings():
    settings = Settings(_env_file=None)
    assert settings.forbidden_project_names == ("AI-메모리",)
    assert Settings(
        _env_file=None, development_forbidden_project_paths="AI-메모리, 비밀 ,"
    ).forbidden_project_names == ("AI-메모리", "비밀")


async def test_project_row_survives_a_restart_without_losing_edits(tmp_path):
    repository = make_repository(tmp_path)
    engine, factory = await make_session(tmp_path)
    settings = Settings(
        _env_file=None,
        development_mode_enabled=True,
        development_repository_root=repository,
        development_project_path=PROJECT,
    )

    async with factory() as session:
        created = await register_configured_project(session, settings)
        assert created is not None
        created.dependency_paths = ["공용/설정"]
        await session.commit()

    async with factory() as session:
        again = await register_configured_project(session, settings)
        assert again.dependency_paths == ["공용/설정"]
        rows = (await session.scalars(__import__("sqlalchemy").select(Project))).all()
        assert len(rows) == 1

    await engine.dispose()


# --- 동시 시작 ---------------------------------------------------------------

class StaleReadSession:
    """다른 프로세스의 INSERT 를 아직 못 본 상태를 흉내 낸다.

    API 와 워커가 동시에 처음 뜨면 둘 다 '없음'을 보고 넣는다.
    """

    def __init__(self, session) -> None:
        self._session = session
        self._blind_get = True
        self._blind_scalar = True

    async def get(self, *args, **kwargs):
        if self._blind_get:
            self._blind_get = False
            return None
        return await self._session.get(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        if self._blind_scalar:
            self._blind_scalar = False
            return None
        return await self._session.scalar(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._session, name)


async def test_concurrent_first_start_does_not_fail_the_second_process(tmp_path):
    repository = make_repository(tmp_path)
    engine, factory = await make_session(tmp_path)

    async with factory() as session:
        first = await ensure_default_project(
            session, git=executor(tmp_path), repository_root=repository, project_path=PROJECT
        )

    async with factory() as session:
        second = await ensure_default_project(
            StaleReadSession(session),
            git=executor(tmp_path),
            repository_root=repository,
            project_path=PROJECT,
        )
    assert second.id == first.id

    async with factory() as session:
        rows = (await session.scalars(__import__("sqlalchemy").select(Project))).all()
    assert len(rows) == 1

    await engine.dispose()


# --- 금지 목록 ---------------------------------------------------------------

def test_forbidden_list_can_only_grow():
    """설정으로 AI-메모리 를 금지 목록에서 뺄 수 없어야 한다."""
    assert "AI-메모리" in Settings(_env_file=None).forbidden_project_names
    added = Settings(_env_file=None, development_forbidden_project_paths="비밀, 개인키 ,")
    assert added.forbidden_project_names == ("AI-메모리", "비밀", "개인키")
    # 같은 이름을 다시 넣어도 중복되지 않는다.
    assert Settings(
        _env_file=None, development_forbidden_project_paths="ai-메모리"
    ).forbidden_project_names == ("AI-메모리",)


def test_forbidden_comparison_ignores_letter_case(tmp_path):
    repository = tmp_path / "repo"
    (repository / "Secrets").mkdir(parents=True)
    (repository / "Secrets" / "a.txt").write_text("a", encoding="utf-8")
    git(repository, "init")
    git(repository, "config", "user.email", "owner@example.com")
    git(repository, "config", "user.name", "주인")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "initial")

    with pytest.raises(ProjectRegistrationError) as caught:
        verify_project_location(executor(tmp_path), repository, "Secrets", ("secrets",))
    assert caught.value.code == "PROJECT_PATH_FORBIDDEN"


def test_location_check_ignores_inherited_git_environment(tmp_path, monkeypatch):
    repository = make_repository(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "없는-저장소" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "없는-작업트리"))

    assert verify_project_location(
        executor(tmp_path), repository, PROJECT, ("AI-메모리",)
    ) == repository / PROJECT
