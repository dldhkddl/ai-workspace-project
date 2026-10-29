from app.models import (
    CONNECTION_STATUSES,
    TASK_STATUSES,
    WORK_STATUSES,
    Base,
    EmployeeConnection,
    EmployeeWorkState,
    QueueTask,
)


def test_status_literals_match_frontend_contract():
    assert set(CONNECTION_STATUSES) == {
        "not_connected", "setup_required", "awaiting_first_sync", "healthy", "delayed", "error", "stopped",
    }
    assert set(WORK_STATUSES) == {"idle", "working", "reviewing", "awaiting_approval", "completed", "failed"}


def test_schema_contains_four_required_tables_and_constraints():
    assert {"teams", "employees", "employee_connections", "employee_work_states"}.issubset(Base.metadata.tables)
    assert EmployeeConnection.__table__.c.status.nullable is False
    assert EmployeeWorkState.__table__.c.status.nullable is True


def test_schema_contains_task_queue_contract():
    assert set(TASK_STATUSES) == {
        "queued", "running", "cancel_requested", "cancelled", "completed", "failed",
    }
    assert "tasks" in Base.metadata.tables
    assert QueueTask.__table__.c.idempotency_key.unique is True
    assert QueueTask.__table__.c.status.nullable is False
    assert QueueTask.__table__.c.attempt_count.nullable is False
    assert QueueTask.__table__.c.max_attempts.nullable is False
