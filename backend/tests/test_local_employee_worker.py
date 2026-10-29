from pathlib import Path


def test_local_employee_handler_builds_a_runner_per_project(tmp_path):
    """실행기는 전역 설정이 아니라 작업에 묶인 프로젝트 위치로 만들어져야 한다.

    하나로 고정해 두면 프로젝트가 둘 이상 등록됐을 때
    A 프로젝트 작업이 B 프로젝트 저장소에서 실행될 수 있다.
    """
    from app.config import Settings
    from app.services.development_workspace import DevelopmentWorkspaceRunner
    from app.services.local_employees import LocalEmployeeHandler
    from app.services.project_registry import ProjectLocation
    from app.worker import build_local_employee_handler

    settings = Settings(
        _env_file=None,
        agent_memory_root=tmp_path / "AI-메모리",
        development_repository_root=tmp_path / "설정에-적힌-저장소",
        development_project_path="설정에-적힌-프로젝트",
        development_worktree_root=tmp_path / "worktrees",
    )
    handler = build_local_employee_handler(settings)

    assert isinstance(handler, LocalEmployeeHandler)
    assert handler.model == settings.codex_employee_model
    assert handler.development_enabled is False

    first = handler.development_runner_factory(
        ProjectLocation(id="가", repository_root=tmp_path / "저장소-가", project_path="프로젝트-가")
    )
    second = handler.development_runner_factory(
        ProjectLocation(id="나", repository_root=tmp_path / "저장소-나", project_path="프로젝트-나")
    )

    assert isinstance(first, DevelopmentWorkspaceRunner)
    assert first.repository_root == (tmp_path / "저장소-가").resolve()
    assert first.project_path == "프로젝트-가"
    assert second.repository_root == (tmp_path / "저장소-나").resolve()
    assert second.project_path == "프로젝트-나"
    # 설정에 적힌 경로가 실행에 쓰이지 않는다.
    assert first.repository_root != settings.development_repository_root.resolve()
    # 작업공간도 프로젝트별로 갈라진다.
    assert first.worktree_root != second.worktree_root
    assert Path(first.worktree_root).name == "가"
