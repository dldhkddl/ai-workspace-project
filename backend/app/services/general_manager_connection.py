from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EmployeeConnection


class GeneralManagerConnectionService:
    EMPLOYEE_ID = "general-manager"
    SOURCE = "OpenAI Responses API"

    @classmethod
    async def mark_configuration(
        cls,
        session: AsyncSession,
        *,
        configured: bool,
        source: str | None = None,
        error: dict | None = None,
    ) -> None:
        connection = await cls._get(session)
        effective_source = source or cls.SOURCE
        same_source = connection.source == effective_source
        connection.source = effective_source
        connection.last_checked_at = datetime.now(timezone.utc)
        if configured:
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
            connection.error = error or {
                "code": "OPENAI_NOT_CONFIGURED",
                "message": "OpenAI API 키 설정이 필요합니다.",
            }
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
        connection.status = "delayed" if retryable else (
            "setup_required" if code in (
                "OPENAI_NOT_CONFIGURED",
                "OPENAI_AUTH_FAILED",
                "CODEX_NOT_INSTALLED",
                "CODEX_NOT_AUTHENTICATED",
            ) else "error"
        )
        connection.error = {"code": code, "message": message}
        await session.commit()

    @classmethod
    async def _get(cls, session: AsyncSession) -> EmployeeConnection:
        connection = await session.get(EmployeeConnection, cls.EMPLOYEE_ID)
        if connection is None:
            raise RuntimeError("general manager connection is not seeded")
        return connection
