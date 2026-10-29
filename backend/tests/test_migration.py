import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="로컬 PostgreSQL 통합 테스트")
def test_alembic_upgrade_creates_office_tables():
    backend_dir = Path(__file__).parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    command.upgrade(config, "head")

    async def migration_contract() -> tuple[set[str], set[str], set[str], str | None, str | None]:
        connection = await asyncpg.connect("postgresql://postgres:postgres@localhost:54322/postgres", ssl=False)
        try:
            rows = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            indexes = await connection.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            constraints = await connection.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid IN ('public.tasks'::regclass, 'public.alerts'::regclass)"
            )
            timestamp_type = await connection.fetchval(
                "SELECT data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'tasks' AND column_name = 'created_at'"
            )
            event_id_type = await connection.fetchval(
                "SELECT data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'realtime_events' AND column_name = 'id'"
            )
            return (
                {row["tablename"] for row in rows},
                {row["indexname"] for row in indexes},
                {row["conname"] for row in constraints},
                timestamp_type,
                event_id_type,
            )
        finally:
            await connection.close()

    tables, indexes, constraints, timestamp_type, event_id_type = asyncio.run(migration_contract())
    assert {
        "teams", "employees", "employee_connections", "employee_work_states", "tasks",
        "activities", "alerts", "audit_logs", "realtime_events",
    }.issubset(tables)
    assert {"ix_tasks_status_created_at", "ix_tasks_employee_id"}.issubset(indexes)
    assert {
        "ix_activities_created_at", "ix_activities_employee_created_at", "ix_activities_task_id",
        "ix_alerts_created_at", "ix_alerts_employee_resolved", "ix_alerts_task_id",
        "ix_audit_logs_created_at", "ix_audit_logs_resource", "ix_realtime_events_created_at",
    }.issubset(indexes)
    assert {
        "ck_task_status", "ck_task_attempt_count", "ck_task_max_attempts",
        "tasks_idempotency_key_key", "ck_alert_severity",
    }.issubset(constraints)
    assert timestamp_type == "timestamp with time zone"
    assert event_id_type == "bigint"
