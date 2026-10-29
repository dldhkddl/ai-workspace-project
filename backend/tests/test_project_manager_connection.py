import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EmployeeConnection
from app.seed import seed_database


@pytest.mark.asyncio
async def test_project_manager_connection_tracks_agent_and_openai_configuration():
    from app.services.project_manager_connection import ProjectManagerConnectionService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed_database(session)

        await ProjectManagerConnectionService.mark_configuration(
            session,
            error={"code": "AGENT_INSTRUCTIONS_NOT_FOUND", "message": "에이전트 파일이 필요합니다."},
        )
        connection = await session.get(EmployeeConnection, "project-manager")
        assert connection.status == "setup_required"
        assert connection.source == "OpenAI Responses API · agents-orchestrator"
        assert connection.error["code"] == "AGENT_INSTRUCTIONS_NOT_FOUND"

        await ProjectManagerConnectionService.mark_configuration(session, error=None)
        assert connection.status == "awaiting_first_sync"
        assert connection.error is None

        await ProjectManagerConnectionService.mark_success(session)
        assert connection.status == "healthy"
        assert connection.last_success_at is not None

        await ProjectManagerConnectionService.mark_failure(
            session,
            code="CODEX_NOT_AUTHENTICATED",
            message="Codex에서 ChatGPT 로그인이 필요합니다.",
            retryable=False,
            source="Codex SDK · ChatGPT · agents-orchestrator",
        )
        await ProjectManagerConnectionService.mark_configuration(
            session,
            error=None,
            source="OpenAI Responses API · agents-orchestrator",
        )
        assert connection.status == "awaiting_first_sync"
        assert connection.error is None
        assert connection.source == "OpenAI Responses API · agents-orchestrator"

    await engine.dispose()
