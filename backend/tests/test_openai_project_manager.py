import json

import httpx
import pytest
from pydantic import ValidationError


def task_context(employee_id: str = "project-manager"):
    from app.worker import TaskContext

    return TaskContext(
        id="task-pm-1",
        employee_id=employee_id,
        task_type="project_plan",
        title="주문 오류 개선 계획",
        payload={"problem": "주문 상태 오류가 반복됨", "deadline": "이번 주"},
        correlation_id="correlation-pm-1",
        attempt_count=1,
    )


def test_default_agent_memory_root_contains_orchestrator(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("AGENT_MEMORY_ROOT", raising=False)
    settings = Settings(_env_file=None)

    assert (settings.agent_memory_root / ".claude" / "agents" / "agents-orchestrator.md").is_file()


def test_project_manager_excludes_temporarily_unavailable_obsidian_employee():
    from app.services.openai_project_manager import (
        PROJECT_PLAN_SCHEMA,
        SELECTABLE_EMPLOYEES,
        ProjectPlan,
    )

    unavailable = {
        "obsidian-knowledge", "scenario-writer", "story-editor", "prompt-director", "shorts-producer",
    }
    assert unavailable.isdisjoint(SELECTABLE_EMPLOYEES)
    assert unavailable.isdisjoint(
        PROJECT_PLAN_SCHEMA["properties"]["assignments"]["items"]["properties"]["employee_id"]["enum"]
    )
    with pytest.raises(ValidationError):
        ProjectPlan.model_validate({
            "objective": "지식 관리 계획",
            "completion_criteria": ["완료"],
            "assignments": [
                {"employee_id": "obsidian-knowledge", "task": "정리", "expected_output": "문서"},
                {"employee_id": "research-factcheck", "task": "검토", "expected_output": "검토 결과"},
            ],
            "risks": [],
            "needs_user_choice": False,
            "user_question": None,
            "user_choices": [],
        })


def test_project_plan_accepts_structured_user_choices():
    from app.services.openai_project_manager import ProjectPlan

    plan = ProjectPlan.model_validate({
        "objective": "동기화 범위를 정한다.",
        "completion_criteria": ["범위가 확정된다."],
        "assignments": [
            {"employee_id": "research-factcheck", "task": "비교", "expected_output": "비교표"},
            {"employee_id": "quality-assurance", "task": "검증", "expected_output": "검증 결과"},
        ],
        "risks": [],
        "needs_user_choice": True,
        "user_question": "자동화 범위를 선택해 주세요.",
        "user_choices": ["개인용 백업", "여러 기기 동기화", "웹 발행 포함"],
    })

    assert plan.user_choices == ["개인용 백업", "여러 기기 동기화", "웹 발행 포함"]


@pytest.mark.asyncio
async def test_project_manager_uses_orchestrator_agent_and_returns_valid_assignments(tmp_path):
    from app.services.openai_project_manager import OpenAIProjectManagerHandler

    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text("# AgentsOrchestrator\nQUALITY-GATE-MARKER", encoding="utf-8")
    captured = {}
    plan = {
        "objective": "주문 상태 오류의 원인을 찾고 수정안을 검증한다.",
        "completion_criteria": ["재현 테스트가 통과한다.", "회귀 테스트가 통과한다."],
        "assignments": [
            {
                "employee_id": "backend-integration",
                "task": "API와 DB 상태 흐름을 조사한다.",
                "expected_output": "원인과 수정안",
            },
            {
                "employee_id": "quality-assurance",
                "task": "재현 및 회귀 테스트를 작성한다.",
                "expected_output": "테스트 결과",
            },
        ],
        "risks": ["운영 데이터 재현이 어려울 수 있다."],
        "needs_user_choice": False,
        "user_question": None,
    }

    async def respond(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_pm_123",
                "model": "gpt-5.6-sol",
                "status": "completed",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(plan, ensure_ascii=False)}],
                }],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIProjectManagerHandler(
            api_key="test-key",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            agent_path=agent_path,
            client=client,
        )
        result = await handler(task_context())

    assert result == {
        "plan": plan,
        "agent": "agents-orchestrator",
        "model": "gpt-5.6-sol",
        "response_id": "resp_pm_123",
    }
    assert "QUALITY-GATE-MARKER" in captured["body"]["instructions"]
    assert "backend-integration" in captured["body"]["instructions"]
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
    assert captured["body"]["text"]["format"]["strict"] is True
    assert captured["body"]["model"] == "gpt-5.6-sol"
    assert captured["body"]["reasoning"] == {"effort": "medium"}
    assert captured["body"]["store"] is False


@pytest.mark.asyncio
async def test_project_manager_rejects_unknown_or_duplicate_employees(tmp_path):
    from app.services.openai_project_manager import OpenAIProjectManagerHandler
    from app.services.task_errors import TaskHandlerError

    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text("# AgentsOrchestrator", encoding="utf-8")
    invalid_plan = {
        "objective": "잘못된 계획",
        "completion_criteria": ["완료"],
        "assignments": [
            {"employee_id": "missing", "task": "작업", "expected_output": "결과"},
            {"employee_id": "missing", "task": "작업", "expected_output": "결과"},
        ],
        "risks": [],
        "needs_user_choice": False,
        "user_question": None,
    }

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_invalid",
                "model": "gpt-5.4-mini",
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(invalid_plan)}]}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIProjectManagerHandler(
            api_key="test-key",
            model="gpt-5.4-mini",
            agent_path=agent_path,
            client=client,
        )
        with pytest.raises(TaskHandlerError) as caught:
            await handler(task_context())

    assert caught.value.code == "OPENAI_INVALID_RESPONSE"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_project_manager_requires_agent_file(tmp_path):
    from app.services.openai_project_manager import OpenAIProjectManagerHandler
    from app.services.task_errors import TaskHandlerError

    handler = OpenAIProjectManagerHandler(
        api_key="test-key",
        model="gpt-5.4-mini",
        agent_path=tmp_path / "missing.md",
    )

    with pytest.raises(TaskHandlerError) as caught:
        await handler(task_context())

    assert caught.value.code == "AGENT_INSTRUCTIONS_NOT_FOUND"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_project_manager_rejects_wrong_assignee(tmp_path):
    from app.services.openai_project_manager import OpenAIProjectManagerHandler
    from app.services.task_errors import TaskHandlerError

    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text("# AgentsOrchestrator", encoding="utf-8")
    handler = OpenAIProjectManagerHandler(api_key="test-key", model="gpt-5.4-mini", agent_path=agent_path)

    with pytest.raises(TaskHandlerError) as caught:
        await handler(task_context("frontend"))

    assert caught.value.code == "INVALID_TASK_ASSIGNEE"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_project_manager_refusal_is_non_retryable(tmp_path):
    from app.services.openai_project_manager import OpenAIProjectManagerHandler
    from app.services.task_errors import TaskHandlerError

    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text("# AgentsOrchestrator", encoding="utf-8")

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_refused",
                "model": "gpt-5.4-mini",
                "status": "completed",
                "output": [{"content": [{"type": "refusal", "refusal": "sensitive refusal detail"}]}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIProjectManagerHandler(
            api_key="test-key",
            model="gpt-5.4-mini",
            agent_path=agent_path,
            client=client,
        )
        with pytest.raises(TaskHandlerError) as caught:
            await handler(task_context())

    assert caught.value.code == "OPENAI_REFUSED"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_project_manager_blocks_agent_instruction_excerpt(tmp_path):
    from app.services.openai_project_manager import OpenAIProjectManagerHandler
    from app.services.task_errors import TaskHandlerError

    secret_excerpt = 'PRIVATE "quoted" \\ instruction ' * 5
    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text(f"# AgentsOrchestrator\n{secret_excerpt}", encoding="utf-8")
    leaked_plan = {
        "objective": secret_excerpt,
        "completion_criteria": ["완료"],
        "assignments": [
            {"employee_id": "backend-integration", "task": "조사", "expected_output": "결과"},
            {"employee_id": "quality-assurance", "task": "검증", "expected_output": "결과"},
        ],
        "risks": [],
        "needs_user_choice": False,
        "user_question": None,
    }

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_leak",
                "model": "gpt-5.4-mini",
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(leaked_plan)}]}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIProjectManagerHandler(
            api_key="test-key",
            model="gpt-5.4-mini",
            agent_path=agent_path,
            client=client,
        )
        with pytest.raises(TaskHandlerError) as caught:
            await handler(task_context())

    assert caught.value.code == "AGENT_INSTRUCTIONS_EXPOSED"
    assert caught.value.retryable is False
    assert secret_excerpt not in caught.value.message


def test_project_manager_configuration_rejects_empty_agent_file(tmp_path):
    from app.services.openai_project_manager import OpenAIProjectManagerHandler

    agent_path = tmp_path / "agents-orchestrator.md"
    agent_path.write_text("   ", encoding="utf-8")
    handler = OpenAIProjectManagerHandler(api_key="test-key", model="gpt-5.4-mini", agent_path=agent_path)

    assert handler.configuration_error()["code"] == "AGENT_INSTRUCTIONS_NOT_FOUND"
