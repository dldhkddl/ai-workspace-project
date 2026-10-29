from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import Employee, Team
from ..schemas import ConnectionResponse, EmployeeResponse, OfficeResponse, TeamResponse
from ..services.office import OfficeService

router = APIRouter()


def employee_response(employee: Employee) -> EmployeeResponse:
    return EmployeeResponse(
        id=employee.id,
        name=employee.name,
        team_id=employee.team_id,
        role=employee.role,
        icon=employee.icon,
        summary=employee.summary,
        allowed_tools=employee.allowed_tools,
        approval_required=employee.approval_required,
        planned_source=employee.planned_source,
        planned_features=employee.planned_features,
        connection=employee.connection,
        work=employee.work,
    )


def team_response(team: Team) -> TeamResponse:
    return TeamResponse(
        id=team.id,
        name=team.name,
        short_name=team.short_name,
        color=team.color,
        employee_ids=[employee.id for employee in team.employees],
    )


@router.get("/version")
async def version() -> dict[str, str]:
    settings = get_settings()
    return {"service": settings.service_name, "version": settings.version}


@router.get("/office", response_model=OfficeResponse)
async def office(session: AsyncSession = Depends(get_session)) -> OfficeResponse:
    teams = await OfficeService.list_teams(session)
    employees = await OfficeService.list_employees(session)
    return OfficeResponse(teams=[team_response(team) for team in teams], employees=[employee_response(employee) for employee in employees])


@router.get("/teams", response_model=list[TeamResponse])
async def teams(team_id: str | None = Query(default=None), session: AsyncSession = Depends(get_session)) -> list[TeamResponse]:
    return [team_response(team) for team in await OfficeService.list_teams(session, team_id)]


@router.get("/employees", response_model=list[EmployeeResponse])
async def employees(team_id: str | None = Query(default=None), session: AsyncSession = Depends(get_session)) -> list[EmployeeResponse]:
    return [employee_response(employee) for employee in await OfficeService.list_employees(session, team_id)]


@router.get("/employees/{employee_id}", response_model=EmployeeResponse)
async def employee(employee_id: str, session: AsyncSession = Depends(get_session)) -> EmployeeResponse | JSONResponse:
    found = await OfficeService.get_employee(session, employee_id)
    if found is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "EMPLOYEE_NOT_FOUND", "message": "직원을 찾을 수 없습니다."}},
        )
    return employee_response(found)


@router.get("/connections", response_model=list[ConnectionResponse])
async def connections(session: AsyncSession = Depends(get_session)) -> list[ConnectionResponse]:
    employees = await OfficeService.list_employees(session)
    return [ConnectionResponse.model_validate(employee.connection) for employee in employees]
