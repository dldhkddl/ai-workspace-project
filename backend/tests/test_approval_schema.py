import pytest

from app.models import (
    ACTIVE_APPROVAL_STATUSES,
    APPROVAL_STATUSES,
    PROJECT_STATUSES,
    Approval,
    Base,
    Project,
)
from app.services.verification_policy import DEFAULT_VERIFICATION_POLICY, verification_policy_hash


def test_project_and_approval_tables_exist():
    assert {"projects", "approvals"}.issubset(Base.metadata.tables)
    assert set(PROJECT_STATUSES) == {"active", "paused"}
    assert set(APPROVAL_STATUSES) == {
        "pending", "approved", "applying", "blocked", "applied",
        "rejected", "invalidated", "expired",
    }
    # blocked 도 아직 결론이 나지 않은 승인이라 같은 결과에 둘이 있으면 안 된다.
    assert set(ACTIVE_APPROVAL_STATUSES) == {"pending", "approved", "applying", "blocked"}
    assert set(ACTIVE_APPROVAL_STATUSES).issubset(APPROVAL_STATUSES)


def test_project_stores_a_structured_policy_not_a_shell_string():
    columns = Project.__table__.c
    assert "verify_command" not in columns
    assert columns.verification_policy.nullable is False
    assert columns.verification_policy_hash.nullable is False

    project = Project(
        id="samplebusiness-ai-company",
        name="AI 운영센터",
        repository_root="C:/repo",
        project_path="AI-company",
    )
    assert project.verification_policy is None  # 기본값은 flush 시점에 채워진다
    assert columns.verification_policy.default.arg(None) == DEFAULT_VERIFICATION_POLICY
    assert columns.verification_policy_hash.default.arg(None) == verification_policy_hash(
        DEFAULT_VERIFICATION_POLICY
    )


def test_approval_is_bound_to_immutable_commits_and_hashes():
    columns = Approval.__table__.c
    for required in (
        "project_id", "task_id", "thread_id", "base_sha", "result_sha", "result_tree",
        "impact_hash", "impact_hash_version", "impact_scope", "impact_scope_hash",
        "revision", "target_branch", "target_sha_at_approval", "verification_policy_hash",
        "last_apply_error", "apply_attempt",
        "status", "expires_at", "has_sensitive_changes", "sensitive_files",
        "patch_path", "patch_sha256", "actor_id",
    ):
        assert required in columns, required

    for not_null in ("project_id", "task_id", "result_sha", "result_tree", "impact_hash",
                     "impact_scope", "impact_scope_hash", "revision",
                     "verification_policy_hash", "has_sensitive_changes"):
        assert columns[not_null].nullable is False, not_null

    constraints = {constraint.name for constraint in Approval.__table__.constraints}
    # 원본 HEAD 만 움직여 재승인할 때 같은 result_sha 로 새 행이 필요하다.
    assert "uq_approvals_task_result_revision" in constraints
    assert "ck_approval_status" in constraints
    assert "ck_approval_revision" in constraints

    indexes = {index.name: index for index in Approval.__table__.indexes}
    assert indexes["uq_approvals_active_result"].unique is True


def test_project_path_is_unique_per_repository():
    constraints = {constraint.name for constraint in Project.__table__.constraints}
    assert "uq_projects_repository_path" in constraints


def approval_row(session, *, task_id="task-1", result_sha="b" * 40, revision=1, status="pending"):
    from app.models import Approval

    approval = Approval(
        project_id="samplebusiness-ai-company",
        task_id=task_id,
        thread_id="thread-1",
        branch="agent/frontend/task-1",
        base_sha="a" * 40,
        result_sha=result_sha,
        result_tree="c" * 40,
        impact_hash="d" * 64,
        impact_scope={"version": 1, "project_path": "AI-company", "dependency_paths": []},
        impact_scope_hash="e" * 64,
        revision=revision,
        verification_policy_hash="f" * 64,
        status=status,
    )
    session.add(approval)
    session.commit()
    return approval


def test_only_one_live_approval_per_result_is_allowed(tmp_path):
    """살아 있는 승인이 둘이면 같은 변경을 두 번 적용할 수 있다.

    서비스 잠금만 두면 다른 경로로 들어온 삽입을 막지 못하므로 DB 에서도 막는다.
    """
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'approvals.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        first = approval_row(session)

        with pytest.raises(IntegrityError):
            approval_row(session, revision=2)
        session.rollback()

        # 끝난 승인은 이력으로 남고, 같은 결과에 새 승인을 다시 받을 수 있다.
        session.get(type(first), first.id).status = "invalidated"
        session.commit()
        approval_row(session, revision=2)

        live = session.scalars(
            sa.select(Approval).where(Approval.status.in_(ACTIVE_APPROVAL_STATUSES))
        ).all()
        assert len(live) == 1
        assert live[0].revision == 2

    engine.dispose()
