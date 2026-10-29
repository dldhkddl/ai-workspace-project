import asyncio

from sqlalchemy import select


PLAN = {
    "objective": "신제품 출시 계획을 완성한다.",
    "completion_criteria": ["시장 근거와 실행안이 함께 있다."],
    "assignments": [
        {
            "employee_id": "research-factcheck",
            "task": "시장 자료를 조사한다.",
            "expected_output": "근거가 표시된 조사 결과",
        },
        {
            "employee_id": "marketing-lead",
            "task": "출시 전략을 만든다.",
            "expected_output": "우선순위가 있는 실행안",
        },
    ],
    "risks": ["근거 자료가 부족할 수 있다."],
    "needs_user_choice": False,
    "user_question": None,
}


def create_completed_plan(client, *, plan=PLAN):
    created = client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "workflow-plan"},
        json={
            "employee_id": "project-manager",
            "task_type": "project_plan",
            "title": "신제품 출시 계획",
            "payload": {"request": "출시 계획을 세워줘"},
        },
    ).json()

    async def complete():
        from app.models import EmployeeConnection, QueueTask

        async with client.app.state.session_factory() as session:
            task = await session.get(QueueTask, created["id"])
            task.status = "completed"
            task.result = {"plan": plan, "agent": "agents-orchestrator"}
            for employee_id in ("research-factcheck", "marketing-lead"):
                connection = await session.get(EmployeeConnection, employee_id)
                connection.status = "healthy"
            await session.commit()

    asyncio.run(complete())
    return created


def test_completed_pm_plan_dispatches_each_assignment_once(client):
    plan = create_completed_plan(client)

    first = client.post(f"/api/v1/tasks/{plan['id']}/dispatch")
    second = client.post(f"/api/v1/tasks/{plan['id']}/dispatch")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "running"
    assert len(first.json()["assignments"]) == 2
    assert [item["employee_id"] for item in first.json()["assignments"]] == [
        "research-factcheck", "marketing-lead",
    ]
    assert {item["status"] for item in first.json()["assignments"]} == {"queued"}
    assert [item["id"] for item in second.json()["assignments"]] == [
        item["id"] for item in first.json()["assignments"]
    ]

    async def queued_events():
        from app.models import Activity

        async with client.app.state.session_factory() as session:
            return list((await session.scalars(select(Activity).where(
                Activity.task_id.in_([item["id"] for item in first.json()["assignments"]]),
                Activity.event_type == "task.queued",
            ))).all())

    assert len(asyncio.run(queued_events())) == 2


def test_worker_completion_automatically_dispatches_a_pm_plan(client):
    created = client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "workflow-auto-plan"},
        json={
            "employee_id": "project-manager",
            "task_type": "project_plan",
            "title": "자동 회의 계획",
            "payload": {"request": "직원들과 자동으로 회의를 진행해줘"},
        },
    ).json()

    async def complete_and_read_children():
        from app.models import EmployeeConnection, QueueTask
        from app.services.task_queue import TaskQueueService
        from app.worker import build_approval_recorder

        factory = client.app.state.session_factory
        async with factory() as session:
            task = await session.get(QueueTask, created["id"])
            task.status = "running"
            task.locked_by = "worker-auto"
            for employee_id in ("research-factcheck", "marketing-lead"):
                connection = await session.get(EmployeeConnection, employee_id)
                connection.status = "healthy"
            await session.commit()

        async with factory() as session:
            completed = await TaskQueueService.complete(
                session,
                created["id"],
                "worker-auto",
                {"plan": PLAN, "agent": "agents-orchestrator"},
                on_completed=build_approval_recorder(factory),
            )
            assert completed is True

        async with factory() as session:
            return list((await session.scalars(select(QueueTask).where(
                QueueTask.parent_task_id == created["id"],
                QueueTask.task_type == "employee_task",
            ).order_by(QueueTask.idempotency_key))).all())

    children = asyncio.run(complete_and_read_children())

    assert [task.employee_id for task in children] == ["research-factcheck", "marketing-lead"]
    assert {task.status for task in children} == {"queued"}


def test_plan_requiring_user_choice_cannot_be_dispatched(client):
    plan = create_completed_plan(client, plan={
        **PLAN,
        "needs_user_choice": True,
        "user_question": "국내와 해외 중 어디를 우선할까요?",
    })

    response = client.post(f"/api/v1/tasks/{plan['id']}/dispatch")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_PLAN_NEEDS_USER_CHOICE"


def test_completed_pm_plan_with_a_question_is_reported_as_requiring_user_response(client):
    plan = create_completed_plan(client, plan={
        **PLAN,
        "needs_user_choice": True,
        "user_question": "어떤 옵시디언 작업을 원하시나요?",
    })

    detail = client.get(f"/api/v1/tasks/{plan['id']}").json()
    listed = client.get("/api/v1/tasks?employee_id=project-manager&limit=20").json()

    assert detail["status"] == "completed"
    assert detail["requires_user_response"] is True
    assert next(item for item in listed if item["id"] == plan["id"])["requires_user_response"] is True


def test_user_response_creates_linked_pm_follow_up_and_workflow_exposes_it(client):
    plan = create_completed_plan(client, plan={
        **PLAN,
        "needs_user_choice": True,
        "user_question": "국내와 해외 중 어디를 우선할까요?",
    })

    response = client.post(f"/api/v1/tasks/{plan['id']}/respond", json={
        "action": "revise",
        "message": "국내 판매부터 진행하고 해외는 다음 단계로 미뤄 주세요.",
    })

    assert response.status_code == 200
    follow_up = response.json()
    assert follow_up["task_type"] == "project_plan"
    assert follow_up["status"] == "queued"
    assert follow_up["payload"]["previous_plan_task_id"] == plan["id"]
    assert follow_up["payload"]["user_response"] == "국내 판매부터 진행하고 해외는 다음 단계로 미뤄 주세요."
    workflow = client.get(f"/api/v1/tasks/{plan['id']}/workflow").json()
    assert workflow["status"] == "replanning"
    assert workflow["continuation"]["id"] == follow_up["id"]
    assert client.get(f"/api/v1/tasks/{plan['id']}").json()["requires_user_response"] is False
    listed = client.get("/api/v1/tasks?employee_id=project-manager&limit=20").json()
    assert next(item for item in listed if item["id"] == plan["id"])["requires_user_response"] is False


def test_same_pm_response_is_idempotent_but_a_second_answer_is_rejected(client):
    plan = create_completed_plan(client, plan={
        **PLAN,
        "needs_user_choice": True,
        "user_question": "안전안으로 진행할까요?",
    })
    body = {"action": "approve", "message": None}

    first = client.post(f"/api/v1/tasks/{plan['id']}/respond", json=body)
    same = client.post(f"/api/v1/tasks/{plan['id']}/respond", json=body)
    different = client.post(f"/api/v1/tasks/{plan['id']}/respond", json={
        "action": "revise", "message": "범위를 줄여 주세요.",
    })

    assert first.status_code == 200
    assert same.status_code == 200
    assert same.json()["id"] == first.json()["id"]
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "PROJECT_PLAN_ALREADY_RESPONDED"


def test_pm_response_requires_a_completed_plan_with_a_question(client):
    plan = create_completed_plan(client)

    response = client.post(f"/api/v1/tasks/{plan['id']}/respond", json={
        "action": "approve", "message": None,
    })

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_PLAN_RESPONSE_NOT_REQUIRED"


def test_dispatch_rejects_unavailable_assignee_without_partial_tasks(client):
    plan = create_completed_plan(client)

    async def stop_one():
        from app.models import EmployeeConnection

        async with client.app.state.session_factory() as session:
            connection = await session.get(EmployeeConnection, "marketing-lead")
            connection.status = "stopped"
            await session.commit()

    asyncio.run(stop_one())
    response = client.post(f"/api/v1/tasks/{plan['id']}/dispatch")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_ASSIGNEE_UNAVAILABLE"
    workflow = client.get(f"/api/v1/tasks/{plan['id']}/workflow").json()
    assert workflow["status"] == "not_started"
    assert workflow["assignments"] == []


def test_last_terminal_assignment_creates_one_synthesis_task(client):
    plan = create_completed_plan(client)
    workflow = client.post(f"/api/v1/tasks/{plan['id']}/dispatch").json()
    child_ids = [item["id"] for item in workflow["assignments"]]

    async def finish_children():
        from app.models import QueueTask
        from app.services.project_workflow import ProjectWorkflowService

        async with client.app.state.session_factory() as session:
            first = await session.get(QueueTask, child_ids[0])
            first.status = "completed"
            first.result = {"response": "시장 조사 완료"}
            await ProjectWorkflowService.advance_after_terminal(session, first)
            await session.commit()

        async with client.app.state.session_factory() as session:
            second = await session.get(QueueTask, child_ids[1])
            second.status = "failed"
            second.error = {"code": "TEST_FAILURE", "message": "전략 작성 실패"}
            await ProjectWorkflowService.advance_after_terminal(session, second)
            await ProjectWorkflowService.advance_after_terminal(session, second)
            await session.commit()

    asyncio.run(finish_children())
    current = client.get(f"/api/v1/tasks/{plan['id']}/workflow")

    assert current.status_code == 200
    assert current.json()["status"] == "synthesizing"
    assert current.json()["synthesis"]["task_type"] == "project_synthesis"

    async def count_synthesis():
        from app.models import QueueTask

        async with client.app.state.session_factory() as session:
            tasks = list((await session.scalars(select(QueueTask).where(
                QueueTask.parent_task_id == plan["id"],
                QueueTask.task_type == "project_synthesis",
            ))).all())
            return len(tasks)

    assert asyncio.run(count_synthesis()) == 1


def test_cancelling_last_open_assignment_still_starts_synthesis(client):
    plan = create_completed_plan(client)
    workflow = client.post(f"/api/v1/tasks/{plan['id']}/dispatch").json()
    first_id, second_id = [item["id"] for item in workflow["assignments"]]

    async def finish_first():
        from app.models import QueueTask
        from app.services.project_workflow import ProjectWorkflowService

        async with client.app.state.session_factory() as session:
            first = await session.get(QueueTask, first_id)
            first.status = "completed"
            first.result = {"response": "완료"}
            await ProjectWorkflowService.advance_after_terminal(session, first)
            await session.commit()

    asyncio.run(finish_first())
    cancelled = client.post(f"/api/v1/tasks/{second_id}/cancel")

    assert cancelled.status_code == 200
    assert client.get(f"/api/v1/tasks/{plan['id']}/workflow").json()["status"] == "synthesizing"


def test_conflict_review_creates_read_only_rebuttals_and_then_final_synthesis(client):
    plan = create_completed_plan(client)
    workflow = client.post(f"/api/v1/tasks/{plan['id']}/dispatch").json()
    assignment_ids = [item["id"] for item in workflow["assignments"]]

    async def run_flow():
        from app.models import QueueTask
        from app.services.project_workflow import ProjectWorkflowService

        factory = client.app.state.session_factory
        async with factory() as session:
            for index, task_id in enumerate(assignment_ids):
                task = await session.get(QueueTask, task_id)
                task.status = "completed"
                task.result = {"response": f"서로 다른 의견 {index + 1}"}
                await ProjectWorkflowService.advance_after_terminal(session, task)
            await session.commit()

        async with factory() as session:
            conflict_review = await session.scalar(select(QueueTask).where(
                QueueTask.parent_task_id == plan["id"],
                QueueTask.task_type == "project_synthesis",
            ))
            conflict_review.status = "completed"
            conflict_review.result = {"report": {
                "summary": "직원 의견이 충돌합니다.",
                "completed_items": [], "incomplete_items": [], "risks": [], "next_actions": [],
                "needs_discussion": True,
                "conflicts": [{
                    "topic": "출시 순서",
                    "employee_ids": ["research-factcheck", "marketing-lead"],
                    "reason": "국내 우선과 해외 우선 의견이 다릅니다.",
                    "review_question": "상대 근거를 검토하고 최종 우선순위를 제안하세요.",
                }],
            }}
            await ProjectWorkflowService.advance_after_terminal(session, conflict_review)
            await session.commit()

        async with factory() as session:
            discussions = list((await session.scalars(select(QueueTask).where(
                QueueTask.parent_task_id == plan["id"],
                QueueTask.task_type == "employee_discussion",
            ).order_by(QueueTask.employee_id))).all())
            assert [task.employee_id for task in discussions] == ["marketing-lead", "research-factcheck"]
            assert all(task.project_id is None for task in discussions)
            assert all(task.input_payload["meeting_round"] == "rebuttal" for task in discussions)
            for task in discussions:
                task.status = "completed"
                task.result = {"response": "상대 근거를 검토한 반박과 수정 의견"}
                await ProjectWorkflowService.advance_after_terminal(session, task)
            await session.commit()

        async with factory() as session:
            final = await session.scalar(select(QueueTask).where(
                QueueTask.idempotency_key == f"project-synthesis-final:{plan['id']}"
            ))
            return final, list((await session.scalars(select(QueueTask).where(
                QueueTask.parent_task_id == plan["id"],
                QueueTask.task_type == "employee_discussion",
            ))).all())

    final, discussions = asyncio.run(run_flow())

    assert final is not None
    assert final.status == "queued"
    assert final.input_payload["synthesis_phase"] == "final"
    assert len(final.input_payload["discussion_results"]) == 2
    current = client.get(f"/api/v1/tasks/{plan['id']}/workflow").json()
    assert current["status"] == "synthesizing"
    assert {item["id"] for item in current["discussions"]} == {task.id for task in discussions}


def test_matching_employee_results_finish_without_a_rebuttal_round(client):
    plan = create_completed_plan(client)
    workflow = client.post(f"/api/v1/tasks/{plan['id']}/dispatch").json()

    async def finish_without_conflict():
        from app.models import QueueTask
        from app.services.project_workflow import ProjectWorkflowService

        factory = client.app.state.session_factory
        async with factory() as session:
            for task_id in [item["id"] for item in workflow["assignments"]]:
                task = await session.get(QueueTask, task_id)
                task.status = "completed"
                task.result = {"response": "같은 방향의 의견"}
                await ProjectWorkflowService.advance_after_terminal(session, task)
            await session.commit()

        async with factory() as session:
            review = await session.scalar(select(QueueTask).where(
                QueueTask.parent_task_id == plan["id"],
                QueueTask.task_type == "project_synthesis",
            ))
            review.status = "completed"
            review.result = {"report": {
                "summary": "직원 의견이 일치합니다.",
                "completed_items": [], "incomplete_items": [], "risks": [], "next_actions": [],
                "needs_discussion": False, "conflicts": [],
            }}
            await ProjectWorkflowService.advance_after_terminal(session, review)
            await session.commit()

    asyncio.run(finish_without_conflict())

    current = client.get(f"/api/v1/tasks/{plan['id']}/workflow").json()
    assert current["status"] == "completed"
    assert current["discussions"] == []
