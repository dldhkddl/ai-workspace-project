from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EmployeeConnection


class ProjectManagerConnectionService:
    EMPLOYEE_ID = "project-manager"
    SOURCE = "OpenAI Responses API · agents-orchestrator"

    @classmethod
    async def mark_configuration(
        cls,
        session: AsyncSession,
        *,
        error: dict | None,
        source: str | None = None,
    ) -> None:
        connection = await cls._get(session)
        effective_source = source or cls.SOURCE
        same_source = connection.source == effective_source
        connection.source = effective_source
        connection.last_checked_at = datetime.now(timezone.utc)
        if error is None:
            if same_source and connection.error and connection.error.get("code") in {
                "OPENAI_AUTH_FAILED",
                "CODEX_NOT_AUTHENTICATED",
            }:
                await session.commit()
                return
            if connection.status != "healthy":
                connection.status = "awaiting_first_sync"
            connection.error = None
        else:
            connection.status = "setup_required"
            connection.error = error
        await session.commit()

    @classmethod
    async def mark_success(cls, session: AsyncSession, *, source: str | None = None) -> None:
        connection = await cls._get(session)
        now = datetime.now(timezone.utc)
        connection.source = source or cls.SOURCE
        connection.status = "healthy"
        connection.last_checked_at = now
        connection.last_success_at = now
        connection.error = None
        await session.commit()

    @classmethod
    async def mark_failure(
        cls,
        session: AsyncSession,
        *,
        code: str,
        message: str,
        retryable: bool,
        source: str | None = None,
    ) -> None:
        connection = await cls._get(session)
        connection.source = source or cls.SOURCE
        connection.last_checked_at = datetime.now(timezone.utc)
        setup_errors = {
            "OPENAI_NOT_CONFIGURED",
            "OPENAI_AUTH_FAILED",
            "CODEX_NOT_INSTALLED",
            "CODEX_NOT_AUTHENTICATED",
            "AGENT_INSTRUCTIONS_NOT_FOUND",
        }
        connection.status = "delayed" if retryable else ("setup_required" if code in setup_errors else "error")
        connection.error = {"code": code, "message": message}
        await session.commit()

    @classmethod
    async def _get(cls, session: AsyncSession) -> EmployeeConnection:
        connection = await session.get(EmployeeConnection, cls.EMPLOYEE_ID)
        if connection is None:
            raise RuntimeError("project manager connection is not seeded")
        return connection
