"""승인 API 계약.

핵심은 두 가지다. 승인 대상 브랜치·HEAD 를 클라이언트가 정하지 못한다는 것,
그리고 화면이 오래된 상태를 들고 있으면 조용히 통과하지 않는다는 것.
"""
import asyncio

import pytest
import sqlalchemy as sa

from app.models import Approval, Project, QueueTask
from app.services.impact_scope import build_impact_scope, impact_scope_hash
from app.services.verification_policy import (
    DEFAULT_VERIFICATION_POLICY,
    verification_policy_hash,
)


PROJECT_PATH = "AI-company"
SCOPE = build_impact_scope(PROJECT_PATH)
RESULT_SHA = "b" * 40


def seed_approval(client, *, sensitive: bool = False, status: str = "pending") -> str:
    async def insert() -> str:
        async with client.app.state.session_factory() as session:
            session.add(Project(
                id="default", name="기본 프로젝트",
                repository_root="/repo", project_path=PROJECT_PATH, dependency_paths=[],
                verification_policy=dict(DEFAULT_VERIFICATION_POLICY),
                verification_policy_hash=verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
            ))
            session.add(QueueTask(
                id="task-1", employee_id="frontend", project_id="default",
                task_type="employee_task", title="화면 수정", input_payload={},
                idempotency_key="task-1", request_hash="hash", status="completed",
                result={
                    "changed_files": ["AI-company/화면.txt"],
                    "diff_stat": "1 file changed",
                    "patch_preview": "diff --git a b",
                    "patch_preview_truncated": False,
                    "secret_scan_skipped_files": ["AI-company/그림.png"],
                },
            ))
            approval = Approval(
                project_id="default", task_id="task-1", thread_id="task-1",
                branch="agent/frontend/task-1",
                base_sha="a" * 40, result_sha=RESULT_SHA, result_tree="c" * 40,
                impact_hash="d" * 64, impact_scope=SCOPE,
                impact_scope_hash=impact_scope_hash(SCOPE),
                verification_policy_hash=verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
                status=status,
                has_sensitive_changes=sensitive,
                sensitive_files=["AI-company/package.json"] if sensitive else [],
            )
            session.add(approval)
            await session.commit()
            return approval.id

    return asyncio.run(insert())


def decide(client, approval_id: str, action: str, **body):
    payload = {"result_sha": RESULT_SHA, "revision": 1, **body}
    return client.post(f"/api/v1/approvals/{approval_id}/{action}", json=payload)


def test_listing_and_detail_carry_what_the_screen_needs(client):
    approval_id = seed_approval(client)

    listed = client.get("/api/v1/approvals?status=pending")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [approval_id]
    # 목록은 가볍게. 변경 내용은 상세에서 가져간다.
    assert "changed_files" not in listed.json()[0]

    detail = client.get(f"/api/v1/approvals/{approval_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "pending"
    assert body["revision"] == 1
    assert body["task_title"] == "화면 수정"
    assert body["changed_files"] == ["AI-company/화면.txt"]
    assert body["secret_scan_skipped_files"] == ["AI-company/그림.png"]
    assert body["diff_stat"] == "1 file changed"
    assert body["patch_preview_truncated"] is False


def test_missing_approval_is_a_not_found(client):
    assert client.get("/api/v1/approvals/없는-승인").status_code == 404
    response = decide(client, "없는-승인", "approve")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "APPROVAL_NOT_FOUND"


def test_the_client_cannot_choose_the_target_branch(client):
    """대상 브랜치·HEAD 는 요청 본문에 넣을 수 없다."""
    approval_id = seed_approval(client)

    response = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json={
            "result_sha": RESULT_SHA, "revision": 1,
            "target_branch": "공격자-브랜치", "target_sha": "0" * 40,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_a_stale_screen_cannot_approve_something_else(client):
    approval_id = seed_approval(client)

    response = decide(client, approval_id, "approve", result_sha="0" * 40)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPROVAL_TARGET_MISMATCH"


def test_sensitive_changes_need_the_extra_confirmation(client):
    approval_id = seed_approval(client, sensitive=True)

    refused = decide(client, approval_id, "approve")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "SENSITIVE_CONFIRMATION_REQUIRED"

    detail = client.get(f"/api/v1/approvals/{approval_id}").json()
    assert detail["status"] == "pending"
    assert detail["sensitive_files"] == ["AI-company/package.json"]


def test_an_unreadable_repository_stops_the_approval(client):
    """저장소를 읽지 못하면 승인하지 않는다. 없는 경로를 프로젝트로 두고 확인한다."""
    approval_id = seed_approval(client)

    response = decide(client, approval_id, "approve")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TARGET_UNREADABLE"
    assert client.get(f"/api/v1/approvals/{approval_id}").json()["status"] == "pending"


def test_rejecting_moves_the_approval_and_queues_only_cleanup(client):
    approval_id = seed_approval(client)

    response = decide(client, approval_id, "reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    async def queued() -> list[str]:
        async with client.app.state.session_factory() as session:
            return [
                row.task_type for row in
                (await session.scalars(sa.select(QueueTask).where(QueueTask.id != "task-1"))).all()
            ]

    assert asyncio.run(queued()) == ["workspace_cleanup"]

    again = decide(client, approval_id, "reject")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "APPROVAL_NOT_PENDING"


def test_retry_is_only_available_for_a_blocked_approval(client):
    approval_id = seed_approval(client)

    refused = decide(client, approval_id, "retry")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "APPROVAL_NOT_BLOCKED"
