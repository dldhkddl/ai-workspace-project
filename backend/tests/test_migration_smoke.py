"""마이그레이션 DDL 을 PostgreSQL 없이도 확인한다.

test_migration.py 는 실제 PostgreSQL 이 있을 때만 돌기 때문에, 평소에는
마이그레이션이 깨져도 아무도 모른다. SQLite 로 전체 사슬을 한 번 올려
DDL 오류와 모델·마이그레이션 어긋남을 잡는다.
"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import Base


VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"


def apply_all_migrations(connection) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        for path in sorted(VERSIONS.glob("0*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()


def test_every_migration_applies_in_order():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        apply_all_migrations(connection)
        tables = set(sa.inspect(connection).get_table_names())

    assert {
        "teams", "employees", "employee_connections", "employee_work_states", "tasks",
        "activities", "alerts", "audit_logs", "realtime_events", "projects", "approvals",
    }.issubset(tables)


def test_migrations_and_models_agree_on_the_new_tables():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        apply_all_migrations(connection)
        inspector = sa.inspect(connection)
        for table in ("projects", "approvals"):
            migrated = {column["name"] for column in inspector.get_columns(table)}
            declared = set(Base.metadata.tables[table].c.keys())
            assert migrated == declared, f"{table}: 마이그레이션과 모델이 다릅니다 {migrated ^ declared}"


def test_story_production_team_migration_populates_unconnected_employees():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        apply_all_migrations(connection)
        team_ids = set(connection.execute(sa.text("SELECT id FROM teams")).scalars())
        rows = connection.execute(sa.text(
            "SELECT e.id, e.team_id, c.status "
            "FROM employees e JOIN employee_connections c ON c.employee_id = e.id "
            "WHERE e.id IN ('scenario-writer','story-editor','prompt-director')"
        )).all()

    assert "story-production" in team_ids
    assert {row.id for row in rows} == {
        "scenario-writer", "story-editor", "prompt-director",
    }
    assert all(row.team_id == "story-production" for row in rows)
    assert all(row.status == "not_connected" for row in rows)
