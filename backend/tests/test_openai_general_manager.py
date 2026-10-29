import httpx
import pytest


def task_context():
    from app.worker import TaskContext

    return TaskContext(
        id="task-1",
        employee_id="general-manager",
        task_type="general_manager_review",
        title="이번 주 운영 위험 검토",
        payload={"facts": ["반품률 3%", "재고 부족 2건"]},
        correlation_id="correlation-1",
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_general_manager_calls_responses_api_and_returns_review():
    from app.services.openai_general_manager import OpenAIGeneralManagerHandler

    captured = {}

    async def respond(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "model": "gpt-5.6-sol",
                "status": "completed",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "재고 부족 건을 먼저 확인하세요."}],
                }],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIGeneralManagerHandler(
            api_key="test-key",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            client=client,
        )
        result = await handler(task_context())

    assert result == {
        "review": "재고 부족 건을 먼저 확인하세요.",
        "model": "gpt-5.6-sol",
        "response_id": "resp_123",
    }
    assert captured["authorization"] == "Bearer test-key"
    assert '"store":false' in captured["body"]
    assert '"model":"gpt-5.6-sol"' in captured["body"]
    assert '"reasoning":{"effort":"high"}' in captured["body"]
    assert "이번 주 운영 위험 검토" in captured["body"]
    assert "반품률 3%" in captured["body"]


@pytest.mark.asyncio
async def test_missing_api_key_is_non_retryable():
    from app.services.openai_general_manager import OpenAIGeneralManagerHandler
    from app.services.task_errors import TaskHandlerError

    handler = OpenAIGeneralManagerHandler(api_key=None, model="gpt-5.4-mini")

    with pytest.raises(TaskHandlerError) as caught:
        await handler(task_context())

    assert caught.value.code == "OPENAI_NOT_CONFIGURED"
    assert caught.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [(401, "OPENAI_AUTH_FAILED", False), (429, "OPENAI_TEMPORARY_FAILURE", True), (500, "OPENAI_TEMPORARY_FAILURE", True)],
)
async def test_openai_http_failures_are_classified(status_code, code, retryable):
    from app.services.openai_general_manager import OpenAIGeneralManagerHandler
    from app.services.task_errors import TaskHandlerError

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "sensitive provider detail"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIGeneralManagerHandler(api_key="test-key", model="gpt-5.4-mini", client=client)
        with pytest.raises(TaskHandlerError) as caught:
            await handler(task_context())

    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert "sensitive provider detail" not in caught.value.message


@pytest.mark.asyncio
async def test_openai_request_errors_are_retryable():
    from app.services.openai_general_manager import OpenAIGeneralManagerHandler
    from app.services.task_errors import TaskHandlerError

    async def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("sensitive transport detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIGeneralManagerHandler(api_key="test-key", model="gpt-5.4-mini", client=client)
        with pytest.raises(TaskHandlerError) as caught:
            await handler(task_context())

    assert caught.value.code == "OPENAI_TEMPORARY_FAILURE"
    assert caught.value.retryable is True
    assert "sensitive transport detail" not in caught.value.message


@pytest.mark.asyncio
async def test_incomplete_output_limit_response_is_non_retryable():
    from app.services.openai_general_manager import OpenAIGeneralManagerHandler
    from app.services.task_errors import TaskHandlerError

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_incomplete",
                "model": "gpt-5.4-mini",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"content": [{"type": "output_text", "text": "부분 결과"}]}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        handler = OpenAIGeneralManagerHandler(api_key="test-key", model="gpt-5.4-mini", client=client)
        with pytest.raises(TaskHandlerError) as caught:
            await handler(task_context())

    assert caught.value.code == "OPENAI_OUTPUT_LIMIT"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_general_manager_refusal_is_non_retryable():
    from app.services.openai_general_manager import OpenAIGeneralManagerHandler
    from app.services.task_errors import TaskHandlerError

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
        handler = OpenAIGeneralManagerHandler(api_key="test-key", model="gpt-5.4-mini", client=client)
        with pytest.raises(TaskHandlerError) as caught:
            await handler(task_context())

    assert caught.value.code == "OPENAI_REFUSED"
    assert caught.value.retryable is False
    assert "sensitive refusal detail" not in caught.value.message
