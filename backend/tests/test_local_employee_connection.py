import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmployeeConnection
from app.seed import seed_database


@pytest.mark.asyncio
async def test_local_employee_connection_tracks_each_employee():
    from app.services.local_employee_connection import LocalEmployeeConnectionService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed_database(session)
        await LocalEmployeeConnectionService.mark_configuration(
            session, "frontend", source="Codex 테스트", error=None
        )
        connection = await session.get(EmployeeConnection, "frontend")
        assert connection.status == "awaiting_first_sync"
        assert connection.source == "Codex 테스트"

        await LocalEmployeeConnectionService.mark_success(
            session, "frontend", source="Codex 테스트"
        )
        assert connection.status == "healthy"
        assert connection.last_success_at is not None

        await LocalEmployeeConnectionService.mark_failure(
            session,
            "frontend",
            source="Codex 테스트",
            code="CODEX_NOT_AUTHENTICATED",
            message="로그인이 필요합니다.",
            retryable=False,
        )
        assert connection.status == "setup_required"
        assert connection.error["code"] == "CODEX_NOT_AUTHENTICATED"
    await engine.dispose()
