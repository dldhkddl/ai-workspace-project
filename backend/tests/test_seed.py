import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Employee, Team
from app.seed import EMPLOYEE_SEED, TEAM_SEED, seed_database


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_contains_story_production_team_and_twenty_six_employees(session):
    await seed_database(session)
    assert len((await session.scalars(select(Team))).all()) == 10
    employees = (await session.scalars(select(Employee))).all()
    assert len(employees) == 26
    assert len(TEAM_SEED) == 10
    assert len(EMPLOYEE_SEED) == 26
    by_id = {employee.id: employee for employee in employees}
    assert by_id["shorts-producer"].team_id == "story-production"
    assert {
        employee_id for employee_id in ("scenario-writer", "story-editor", "prompt-director")
        if by_id[employee_id].team_id == "story-production"
    } == {"scenario-writer", "story-editor", "prompt-director"}
