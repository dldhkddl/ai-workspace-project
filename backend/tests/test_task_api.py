import asyncio

import pytest
from pydantic import ValidationError

from app.schemas import TaskCreate


TASK_REQUEST = {
    "employee_id": "research-factcheck",
    "task_type": "employee_task",
    "title": "주문 현황 초안",
    "payload": {"range": "today"},
}


def create_task(client, key: str = "task-create-1", body: dict | None = None):
    return client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": key},
        json=body or TASK_REQUEST,
    )


def test_create_list_get_and_cancel_task(client):
    created = create_task(client)

    assert created.status_code == 201
    task = created.json()
    assert task["employee_id"] == "research-factcheck"
    assert task["task_type"] == "employee_task"
    assert task["title"] == "주문 현황 초안"
    assert task["payload"] == {"range": "today"}
    assert task["status"] == "queued"
    assert task["attempt_count"] == 0
    assert task["max_attempts"] == 3

    listed = client.get("/api/v1/tasks?status=queued&employee_id=research-factcheck")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [task["id"]]
    assert "payload" not in listed.json()[0]
    assert "result" not in listed.json()[0]
    assert "error" not in listed.json()[0]

    detail = client.get(f"/api/v1/tasks/{task['id']}")
    assert detail.status_code == 200
    assert detail.json()["correlation_id"] == task["correlation_id"]
    assert detail.json()["payload"] == {"range": "today"}
    assert detail.json()["result"] is None
    assert detail.json()["error"] is None
    assert detail.json()["will_retry"] is False

    cancelled = client.post(f"/api/v1/tasks/{task['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["finished_at"] is not None


def test_same_idempotency_key_and_request_reuses_existing_task(client):
    first = create_task(client, "same-request")
    second = create_task(client, "same-request")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(client.get("/api/v1/tasks").json()) == 1


def test_task_detail_exposes_result_and_safe_retry_state(client):
    task = create_task(client, "task-result").json()

    async def mark_completed():
        from app.models import QueueTask

        async with client.app.state.session_factory() as session:
            stored = await session.get(QueueTask, task["id"])
            stored.status = "completed"
            stored.result = {"review": "재고 부족을 먼저 확인하세요."}
            await session.commit()

    asyncio.run(mark_completed())
    completed = client.get(f"/api/v1/tasks/{task['id']}").json()

    assert completed["result"] == {"review": "재고 부족을 먼저 확인하세요."}
    assert completed["error"] is None
    assert completed["will_retry"] is False


def test_task_detail_exposes_safe_error_and_whether_it_will_retry(client):
    task = create_task(client, "task-retry").json()

    async def mark_retrying():
        from app.models import QueueTask

        async with client.app.state.session_factory() as session:
            stored = await session.get(QueueTask, task["id"])
            stored.attempt_count = 1
            stored.error = {"code": "OPENAI_TEMPORARY_FAILURE", "message": "잠시 후 다시 시도합니다."}
            await session.commit()

    asyncio.run(mark_retrying())
    retrying = client.get(f"/api/v1/tasks/{task['id']}").json()

    assert retrying["error"] == {
        "code": "OPENAI_TEMPORARY_FAILURE",
        "message": "잠시 후 다시 시도합니다.",
    }
    assert retrying["will_retry"] is True


def test_task_detail_marks_permanent_failure_as_not_retrying(client):
    task = create_task(client, "task-permanent-failure").json()

    async def mark_failed():
        from app.models import QueueTask

        async with client.app.state.session_factory() as session:
            stored = await session.get(QueueTask, task["id"])
            stored.status = "failed"
            stored.attempt_count = 1
            stored.error = {"code": "OPENAI_AUTH_FAILED", "message": "OpenAI API 인증에 실패했습니다."}
            await session.commit()

    asyncio.run(mark_failed())
    failed = client.get(f"/api/v1/tasks/{task['id']}").json()

    assert failed["error"]["code"] == "OPENAI_AUTH_FAILED"
    assert failed["will_retry"] is False


def test_general_manager_review_rejects_wrong_employee(client):
    response = create_task(
        client,
        "wrong-general-manager",
        {**TASK_REQUEST, "task_type": "general_manager_review"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TASK_ASSIGNEE"
    assert response.json()["error"]["message"] == "작업 유형과 담당 직원이 일치하지 않습니다."


def test_project_plan_rejects_wrong_employee(client):
    response = create_task(
        client,
        "wrong-project-manager",
        {**TASK_REQUEST, "task_type": "project_plan"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TASK_ASSIGNEE"


def test_unsupported_task_type_is_rejected_before_it_can_wait_forever(client):
    response = create_task(
        client,
        "unsupported-browser-check",
        {**TASK_REQUEST, "task_type": "browser-check"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_TASK_TYPE"
    assert client.get("/api/v1/tasks").json() == []


def test_employee_task_accepts_local_agent_and_rejects_deferred_program(client):
    local = create_task(
        client,
        "local-analysis-task",
        {
            **TASK_REQUEST,
            "employee_id": "research-factcheck",
            "task_type": "employee_task",
        },
    )
    deferred = create_task(
        client,
        "deferred-stock-task",
        {
            **TASK_REQUEST,
            "employee_id": "stock-operator",
            "task_type": "employee_task",
        },
    )

    assert local.status_code == 201
    assert deferred.status_code == 422
    assert deferred.json()["error"]["code"] == "INVALID_TASK_ASSIGNEE"


def test_employee_task_separates_paused_development_from_invalid_assignee(client):
    """개발 직원은 '잘못된 담당자'가 아니라 '정책상 중단'이므로 코드가 달라야 한다."""
    paused = create_task(
        client,
        "paused-frontend-task",
        {
            **TASK_REQUEST,
            "employee_id": "frontend",
            "task_type": "employee_task",
        },
    )

    assert paused.status_code == 403
    assert paused.json()["error"]["code"] == "DEVELOPMENT_MODE_PAUSED"


def test_same_idempotency_key_with_different_request_is_rejected(client):
    assert create_task(client, "conflict-key").status_code == 201
    changed = {**TASK_REQUEST, "title": "다른 작업"}

    conflict = create_task(client, "conflict-key", changed)

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_task_api_validates_header_employee_and_missing_task(client):
    missing_header = client.post("/api/v1/tasks", json=TASK_REQUEST)
    assert missing_header.status_code == 422
    assert missing_header.json() == {
        "error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."},
    }

    missing_employee = create_task(client, "missing-employee", {**TASK_REQUEST, "employee_id": "missing"})
    assert missing_employee.status_code == 404
    assert missing_employee.json()["error"]["code"] == "EMPLOYEE_NOT_FOUND"

    missing_task = client.get("/api/v1/tasks/missing")
    assert missing_task.status_code == 404
    assert missing_task.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_task_api_rejects_unknown_fields_blank_text_and_oversized_payload(client):
    unknown = create_task(client, "unknown-field", {**TASK_REQUEST, "paylod": {}})
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "VALIDATION_ERROR"

    blank = create_task(client, "blank-title", {**TASK_REQUEST, "title": "   "})
    assert blank.status_code == 422

    oversized = create_task(client, "large-payload", {**TASK_REQUEST, "payload": {"text": "x" * 33_000}})
    assert oversized.status_code == 422


def test_task_api_rejects_whitespace_only_idempotency_key(client):
    response = create_task(client, "   ")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("invalid_number", ["NaN", "Infinity", "-Infinity"])
def test_task_api_rejects_non_finite_payload_numbers(client, invalid_number):
    body = (
        '{"employee_id":"smartstore-operator","task_type":"draft",'
        '"title":"비정상 숫자","payload":{"nested":{"value":'
        f"{invalid_number}" + "}}}"
    )

    response = client.post(
        "/api/v1/tasks",
        headers={
            "Idempotency-Key": f"invalid-number-{invalid_number}",
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("invalid_number", [float("nan"), float("inf"), float("-inf")])
def test_task_schema_rejects_nested_non_finite_payload_numbers(invalid_number):
    with pytest.raises(ValidationError):
        TaskCreate(**{**TASK_REQUEST, "payload": {"nested": {"value": invalid_number}}})


def test_task_list_rejects_invalid_filter_with_safe_error(client):
    invalid = client.get("/api/v1/tasks?status=unknown&limit=101")

    assert invalid.status_code == 422
    assert invalid.json() == {
        "error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."},
    }


def test_cancelled_task_cannot_be_cancelled_again(client):
    task = create_task(client, "cancel-once").json()
    assert client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 200

    conflict = client.post(f"/api/v1/tasks/{task['id']}/cancel")

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "TASK_NOT_CANCELLABLE"


def test_task_list_returns_newest_first_and_pages_with_offset(client):
    created = [
        create_task(client, f"order-key-{index}", {**TASK_REQUEST, "title": f"작업 {index}"}).json()["id"]
        for index in range(3)
    ]

    listed = client.get("/api/v1/tasks")
    assert [item["id"] for item in listed.json()] == list(reversed(created))

    first_page = client.get("/api/v1/tasks?limit=2&offset=0")
    second_page = client.get("/api/v1/tasks?limit=2&offset=2")
    assert [item["id"] for item in first_page.json()] == list(reversed(created))[:2]
    assert [item["id"] for item in second_page.json()] == list(reversed(created))[2:]
