import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmployeeConnection
from app.seed import seed_database


@pytest.mark.asyncio
async def test_general_manager_connection_tracks_configuration_success_and_auth_failure():
    from app.services.general_manager_connection import GeneralManagerConnectionService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed_database(session)

        await GeneralManagerConnectionService.mark_configuration(session, configured=False)
        connection = await session.get(EmployeeConnection, "general-manager")
        assert connection.status == "setup_required"
        assert connection.error["code"] == "OPENAI_NOT_CONFIGURED"

        await GeneralManagerConnectionService.mark_configuration(session, configured=True)
        assert connection.status == "awaiting_first_sync"
        assert connection.error is None

        await GeneralManagerConnectionService.mark_success(session)
        assert connection.status == "healthy"
        assert connection.last_success_at is not None

        await GeneralManagerConnectionService.mark_failure(
            session,
            code="OPENAI_AUTH_FAILED",
            message="OpenAI API 인증에 실패했습니다.",
            retryable=False,
        )
        assert connection.status == "setup_required"
        assert connection.error == {
            "code": "OPENAI_AUTH_FAILED",
            "message": "OpenAI API 인증에 실패했습니다.",
        }

        await GeneralManagerConnectionService.mark_configuration(session, configured=True)
        assert connection.status == "setup_required"
        assert connection.error["code"] == "OPENAI_AUTH_FAILED"

        await GeneralManagerConnectionService.mark_configuration(
            session,
            configured=True,
            source="Codex SDK · ChatGPT",
        )
        assert connection.status == "awaiting_first_sync"
        assert connection.error is None
        assert connection.source == "Codex SDK · ChatGPT"

        await GeneralManagerConnectionService.mark_failure(
            session,
            code="CODEX_NOT_AUTHENTICATED",
            message="Codex에서 ChatGPT 로그인이 필요합니다.",
            retryable=False,
            source="Codex SDK · ChatGPT",
        )
        await GeneralManagerConnectionService.mark_configuration(
            session,
            configured=True,
            source="OpenAI Responses API",
        )
        assert connection.status == "awaiting_first_sync"
        assert connection.error is None

    await engine.dispose()
