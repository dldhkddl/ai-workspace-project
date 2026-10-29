from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Employee, Team


class OfficeService:
    @staticmethod
    async def list_teams(session: AsyncSession, team_id: str | None = None) -> list[Team]:
        statement = select(Team).options(selectinload(Team.employees)).order_by(Team.id)
        if team_id:
            statement = statement.where(Team.id == team_id)
        return list((await session.scalars(statement)).unique().all())

    @staticmethod
    async def list_employees(session: AsyncSession, team_id: str | None = None) -> list[Employee]:
        statement = select(Employee).options(selectinload(Employee.connection), selectinload(Employee.work)).order_by(Employee.id)
        if team_id:
            statement = statement.where(Employee.team_id == team_id)
        return list((await session.scalars(statement)).unique().all())

    @staticmethod
    async def get_employee(session: AsyncSession, employee_id: str) -> Employee | None:
        statement = select(Employee).where(Employee.id == employee_id).options(selectinload(Employee.connection), selectinload(Employee.work))
        return await session.scalar(statement)
