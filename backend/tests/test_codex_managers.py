import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def task_context(employee_id: str):
    from app.worker import TaskContext

    return TaskContext(
        id="task-codex-1",
        employee_id=employee_id,
        task_type="project_plan" if employee_id == "project-manager" else "general_manager_review",
        title="운영 오류 개선",
        payload={"problem": "주문 상태 오류가 반복됨"},
        correlation_id="correlation-codex-1",
        attempt_count=1,
    )


class RecordingRunner:
    def __init__(self, final_response: str):
        self.final_response = final_response
        self.request = None

    async def run(self, **request):
        from app.services.codex_managers import CodexRunResult

        self.request = request
        return CodexRunResult(id="turn_123", final_response=self.final_response)


@pytest.mark.asyncio
async def test_codex_general_manager_returns_review_without_api_key(tmp_path):
    from app.services.codex_managers import CodexGeneralManagerHandler

    runner = RecordingRunner("재고 오류부터 확인하세요.")
    handler = CodexGeneralManagerHandler(
        model="gpt-5.6-sol",
        reasoning_effort="high",
        runner=runner,
    )

    result = await handler(task_context("general-manager"))

    assert result == {
        "review": "재고 오류부터 확인하세요.",
        "model": "gpt-5.6-sol",
        "response_id": "turn_123",
    }
    assert runner.request["model"] == "gpt-5.6-sol"
    assert runner.request["reasoning_effort"] == "high"
    assert runner.request["output_schema"] is None
    assert "주문 상태 오류가 반복됨" in runner.request["prompt"]


@pytest.mark.asyncio
async def test_codex_project_manager_enforces_structured_plan(tmp_path):
    from app.services.codex_managers import CodexProjectManagerHandler

    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text("# 프로젝트 관리자\nQUALITY-GATE-MARKER", encoding="utf-8")
    plan = {
        "objective": "주문 상태 오류의 원인을 찾아 수정한다.",
        "completion_criteria": ["회귀 테스트가 통과한다."],
        "assignments": [
            {
                "employee_id": "backend-integration",
                "task": "API와 DB 흐름을 조사한다.",
                "expected_output": "원인과 수정안",
            },
            {
                "employee_id": "quality-assurance",
                "task": "회귀 테스트를 작성한다.",
                "expected_output": "테스트 결과",
            },
        ],
        "risks": [],
        "needs_user_choice": False,
        "user_question": None,
    }
    runner = RecordingRunner(json.dumps(plan, ensure_ascii=False))
    handler = CodexProjectManagerHandler(
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        agent_path=agent_path,
        runner=runner,
    )

    result = await handler(task_context("project-manager"))

    assert result == {
        "plan": plan,
        "agent": "agents-orchestrator",
        "model": "gpt-5.6-sol",
        "response_id": "turn_123",
    }
    assert runner.request["output_schema"]["required"] == [
        "objective",
        "completion_criteria",
        "assignments",
        "risks",
        "needs_user_choice",
        "user_question",
        "user_choices",
    ]
    assert "QUALITY-GATE-MARKER" in runner.request["developer_instructions"]


@pytest.mark.asyncio
async def test_codex_project_manager_synthesizes_assignment_results(tmp_path):
    from app.services.codex_managers import CodexProjectManagerHandler
    from app.worker import TaskContext

    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text("# 프로젝트 관리자", encoding="utf-8")
    report = {
        "summary": "조사는 완료됐고 전략 작업은 실패했습니다.",
        "completed_items": ["시장 조사"],
        "incomplete_items": ["출시 전략"],
        "risks": ["전략 근거가 부족합니다."],
        "next_actions": ["전략 작업을 다시 배분합니다."],
    }
    runner = RecordingRunner(json.dumps(report, ensure_ascii=False))
    handler = CodexProjectManagerHandler(
        model="gpt-5.6-sol", reasoning_effort="medium", agent_path=agent_path, runner=runner,
    )
    context = TaskContext(
        id="synthesis-1", employee_id="project-manager", task_type="project_synthesis",
        title="결과 종합", payload={"assignment_results": []},
        correlation_id="workflow-1", attempt_count=1,
    )

    result = await handler(context)

    assert result["report"] == report
    assert runner.request["output_schema"]["required"] == [
        "summary", "completed_items", "incomplete_items", "risks", "next_actions",
        "needs_discussion", "conflicts",
    ]
    assert "결과를 종합" in runner.request["developer_instructions"]


def test_project_report_requires_conflicts_when_discussion_is_needed():
    from pydantic import ValidationError
    from app.services.openai_project_manager import ProjectReport

    with pytest.raises(ValidationError):
        ProjectReport.model_validate({
            "summary": "의견 충돌",
            "completed_items": [], "incomplete_items": [], "risks": [], "next_actions": [],
            "needs_discussion": True,
            "conflicts": [],
        })


@pytest.mark.asyncio
async def test_codex_failure_is_exposed_without_falling_back_to_openai(tmp_path):
    from app.services.codex_managers import (
        CodexGeneralManagerHandler,
        CodexRunnerError,
    )
    from app.services.task_errors import TaskHandlerError

    class FailingRunner:
        async def run(self, **_request):
            raise CodexRunnerError(
                "CODEX_NOT_AUTHENTICATED",
                "sensitive provider detail",
                retryable=False,
            )

    handler = CodexGeneralManagerHandler(
        model="gpt-5.6-sol",
        reasoning_effort="high",
        runner=FailingRunner(),
    )

    with pytest.raises(TaskHandlerError) as caught:
        await handler(task_context("general-manager"))

    assert caught.value.code == "CODEX_NOT_AUTHENTICATED"
    assert caught.value.retryable is False
    assert "sensitive provider detail" not in caught.value.message


def test_codex_project_manager_requires_agent_file(tmp_path):
    from app.services.codex_managers import CodexProjectManagerHandler

    handler = CodexProjectManagerHandler(
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        agent_path=tmp_path / "missing.md",
        runner=RecordingRunner("{}"),
    )

    assert handler.configuration_error()["code"] == "AGENT_INSTRUCTIONS_NOT_FOUND"


def fake_codex_module(*, account_type: str = "chatgpt"):
    captured = {}

    class FakeThread:
        async def run(self, _prompt, **_kwargs):
            return SimpleNamespace(id="turn-safe", final_response="확인")

    class FakeCodex:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            if "cwd" in captured:
                captured["cwd_exists_during_client_exit"] = Path(captured["cwd"]).exists()
            return None

        async def account(self):
            root = SimpleNamespace(type=account_type)
            return SimpleNamespace(account=SimpleNamespace(root=root))

        async def thread_start(self, **kwargs):
            captured.update(kwargs)
            return FakeThread()

    class FakeCodexError(Exception):
        pass

    module = SimpleNamespace(
        ApprovalMode=SimpleNamespace(deny_all="deny_all"),
        AsyncCodex=FakeCodex,
        CodexError=FakeCodexError,
        Sandbox=SimpleNamespace(read_only="read-only"),
        is_retryable_error=lambda _error: False,
    )
    return module, captured


@pytest.mark.asyncio
async def test_local_codex_runner_disables_file_tools_and_uses_empty_temporary_directory(
    tmp_path, monkeypatch
):
    from app.services.codex_managers import LocalCodexRunner

    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    module, captured = fake_codex_module()
    monkeypatch.setitem(sys.modules, "openai_codex", module)
    runner = LocalCodexRunner(timeout_seconds=10)

    await runner.run(
        prompt="검토",
        developer_instructions="파일을 읽지 마세요.",
        model="gpt-5.6-sol",
        reasoning_effort="low",
        output_schema=None,
    )

    execution_directory = Path(captured["cwd"])
    assert execution_directory != tmp_path
    assert not execution_directory.exists()
    assert captured.pop("cwd_exists_during_client_exit") is True
    assert captured["config"] == {
        "features": {
            "shell_tool": False,
            "unified_exec": False,
            "js_repl": False,
            "browser_use": False,
        },
        "web_search": "disabled",
    }


@pytest.mark.asyncio
async def test_local_codex_runner_rejects_api_key_account(monkeypatch):
    from app.services.codex_managers import LocalCodexRunner, CodexRunnerError

    module, captured = fake_codex_module(account_type="apiKey")
    monkeypatch.setitem(sys.modules, "openai_codex", module)
    runner = LocalCodexRunner(timeout_seconds=10)

    with pytest.raises(CodexRunnerError) as caught:
        await runner.run(
            prompt="검토",
            developer_instructions="파일을 읽지 마세요.",
            model="gpt-5.6-sol",
            reasoning_effort="low",
            output_schema=None,
        )

    assert caught.value.code == "CODEX_NOT_AUTHENTICATED"
    assert caught.value.retryable is False
    assert captured == {}
