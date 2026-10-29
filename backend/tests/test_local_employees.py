from pathlib import Path

import pytest


ENABLED_EMPLOYEES = {
    "research-factcheck",
    "data-analyst",
    "tech-lead",
    "frontend",
    "backend-integration",
    "ai-automation",
    "quality-assurance",
    "devops-security",
    "ui-ux-designer",
    "marketing-lead",
    "content-copy",
    "performance-analyst",
    "customer-inquiry",
    "order-delivery",
    "returns-voc",
    "risk-analyst",
}

DEFERRED_EMPLOYEES = {
    "smartstore-operator",
    "shorts-producer",
    "youtube-channel",
    "stock-operator",
    "obsidian-knowledge",
    "scenario-writer",
    "story-editor",
    "prompt-director",
}


def task_context(employee_id: str, project=None, task_type: str = "employee_task"):
    from app.worker import TaskContext

    return TaskContext(
        id="task-local-1",
        employee_id=employee_id,
        task_type=task_type,
        title="업무 요청",
        payload={"request": "요청 내용을 처리해줘"},
        correlation_id="correlation-local-1",
        attempt_count=1,
        project_id=None if project is None else project.id,
        project=project,
    )


def test_local_employee_profiles_enable_agents_and_defer_program_integrations():
    from app.services.local_employee_profiles import LOCAL_EMPLOYEE_PROFILES

    assert set(LOCAL_EMPLOYEE_PROFILES) == ENABLED_EMPLOYEES
    assert DEFERRED_EMPLOYEES.isdisjoint(LOCAL_EMPLOYEE_PROFILES)
    assert LOCAL_EMPLOYEE_PROFILES["research-factcheck"].mode == "analysis"
    assert LOCAL_EMPLOYEE_PROFILES["risk-analyst"].mode == "analysis"
    assert LOCAL_EMPLOYEE_PROFILES["frontend"].mode == "development"
    assert LOCAL_EMPLOYEE_PROFILES["backend-integration"].mode == "development"
    assert LOCAL_EMPLOYEE_PROFILES["ui-ux-designer"].mode == "development"


def test_every_local_employee_profile_has_an_agent_file():
    from app.config import Settings
    from app.services.local_employee_profiles import LOCAL_EMPLOYEE_PROFILES

    root = Settings(_env_file=None).agent_memory_root / ".claude" / "agents"

    missing = [
        profile.agent_file
        for profile in LOCAL_EMPLOYEE_PROFILES.values()
        if not (root / profile.agent_file).is_file()
    ]
    assert missing == []


class RecordingAnalysisRunner:
    def __init__(self):
        self.request = None

    async def run(self, **request):
        from app.services.codex_managers import CodexRunResult

        self.request = request
        return CodexRunResult(id="turn-analysis", final_response="분석 결과입니다.")


class RecordingDevelopmentRunner:
    def __init__(self):
        self.context = None
        self.profile = None
        self.instructions = None

    async def execute(self, context, profile, instructions):
        self.context = context
        self.profile = profile
        self.instructions = instructions
        return {
            "response": "프론트 코드를 수정하고 테스트했습니다.",
            "workspace": "C:/temp/task-local-1",
            "branch": "agent/frontend/task-local-1",
            "changed_files": ["AI-company/src/App.tsx"],
            "diff_stat": "1 file changed",
            "requires_approval": True,
        }


@pytest.mark.asyncio
async def test_analysis_employee_uses_role_agent_without_workspace_tools(tmp_path):
    from app.services.local_employees import LocalEmployeeHandler

    agent_root = tmp_path / "agents"
    agent_root.mkdir()
    (agent_root / "product-trend-researcher.md").write_text(
        "# 리서치 전문가\n근거를 확인한다.", encoding="utf-8"
    )
    runner = RecordingAnalysisRunner()
    handler = LocalEmployeeHandler(
        agent_root=agent_root,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        analysis_runner=runner,
        development_runner_factory=lambda location: RecordingDevelopmentRunner(),
    )

    result = await handler(task_context("research-factcheck"))

    assert result == {
        "response": "분석 결과입니다.",
        "agent": "product-trend-researcher",
        "mode": "analysis",
        "model": "gpt-5.6-sol",
        "response_id": "turn-analysis",
        "requires_approval": False,
    }
    assert "근거를 확인한다" in runner.request["developer_instructions"]
    assert "요청 내용을 처리해줘" in runner.request["prompt"]


@pytest.mark.asyncio
async def test_development_employee_uses_isolated_workspace_runner(tmp_path):
    from app.services.local_employees import LocalEmployeeHandler

    agent_root = tmp_path / "agents"
    agent_root.mkdir()
    (agent_root / "engineering-frontend-developer.md").write_text(
        "# 프론트엔드 개발자\n테스트를 실행한다.", encoding="utf-8"
    )
    from pathlib import Path

    from app.services.project_registry import ProjectLocation

    development_runner = RecordingDevelopmentRunner()
    handler = LocalEmployeeHandler(
        agent_root=agent_root,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        analysis_runner=RecordingAnalysisRunner(),
        development_runner_factory=lambda location: development_runner,
        development_enabled=True,
    )
    location = ProjectLocation(id="default", repository_root=Path("/repo"), project_path="AI-company")

    result = await handler(task_context("frontend", project=location))

    assert result["mode"] == "development"
    assert result["requires_approval"] is True
    assert result["changed_files"] == ["AI-company/src/App.tsx"]
    assert development_runner.profile.employee_id == "frontend"
    assert "테스트를 실행한다" in development_runner.instructions


@pytest.mark.asyncio
async def test_development_employee_discussion_is_read_only_and_does_not_open_workspace(tmp_path):
    from app.services.local_employees import LocalEmployeeHandler

    agent_root = tmp_path / "agents"
    agent_root.mkdir()
    (agent_root / "engineering-frontend-developer.md").write_text(
        "# 프론트엔드 개발자\n근거를 비교한다.", encoding="utf-8"
    )
    analysis_runner = RecordingAnalysisRunner()
    development_runner = RecordingDevelopmentRunner()
    handler = LocalEmployeeHandler(
        agent_root=agent_root,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        analysis_runner=analysis_runner,
        development_runner_factory=lambda location: development_runner,
        development_enabled=True,
    )

    result = await handler(task_context("frontend", task_type="employee_discussion"))

    assert result["mode"] == "discussion"
    assert result["requires_approval"] is False
    assert analysis_runner.request is not None
    assert development_runner.context is None
    assert "파일·셸 도구를 사용하지 마세요" in analysis_runner.request["developer_instructions"]


@pytest.mark.asyncio
async def test_deferred_program_employee_cannot_use_local_employee_task(tmp_path):
    from app.services.local_employees import LocalEmployeeHandler
    from app.services.task_errors import TaskHandlerError

    handler = LocalEmployeeHandler(
        agent_root=Path(tmp_path),
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        analysis_runner=RecordingAnalysisRunner(),
        development_runner_factory=lambda location: RecordingDevelopmentRunner(),
    )

    with pytest.raises(TaskHandlerError) as caught:
        await handler(task_context("stock-operator"))

    assert caught.value.code == "INVALID_TASK_ASSIGNEE"
    assert caught.value.retryable is False
