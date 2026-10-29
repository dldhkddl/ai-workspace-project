from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EmployeeConnection


class LocalEmployeeConnectionService:
    @classmethod
    async def mark_configuration(
        cls,
        session: AsyncSession,
        employee_id: str,
        *,
        source: str,
        error: dict | None,
        status_on_error: Literal["setup_required", "stopped"] = "setup_required",
    ) -> None:
        connection = await cls._get(session, employee_id)
        connection.source = source
        connection.last_checked_at = datetime.now(timezone.utc)
        if error is None:
            if connection.status != "healthy":
                connection.status = "awaiting_first_sync"
            connection.error = None
        else:
            connection.status = status_on_error
            connection.error = error
        await session.commit()

    @classmethod
    async def mark_success(cls, session: AsyncSession, employee_id: str, *, source: str) -> None:
        connection = await cls._get(session, employee_id)
        now = datetime.now(timezone.utc)
        connection.source = source
        connection.status = "healthy"
        connection.last_checked_at = now
        connection.last_success_at = now
        connection.error = None
        await session.commit()

    @classmethod
    async def mark_failure(
        cls,
        session: AsyncSession,
        employee_id: str,
        *,
        source: str,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        connection = await cls._get(session, employee_id)
        setup_errors = {
            "CODEX_NOT_INSTALLED",
            "CODEX_NOT_AUTHENTICATED",
            "AGENT_INSTRUCTIONS_NOT_FOUND",
        }
        connection.source = source
        connection.last_checked_at = datetime.now(timezone.utc)
        connection.status = "delayed" if retryable else (
            "setup_required" if code in setup_errors else "error"
        )
        connection.error = {"code": code, "message": message}
        await session.commit()

    @staticmethod
    async def _get(session: AsyncSession, employee_id: str) -> EmployeeConnection:
        connection = await session.get(EmployeeConnection, employee_id)
        if connection is None:
            raise RuntimeError("local employee connection is not seeded")
        return connection
