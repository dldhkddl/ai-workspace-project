import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import ActivityResponse, AlertResponse, RealtimeEventResponse
from ..services.event_log import EventLogService

router = APIRouter(tags=["operations"])


@router.get("/activities", response_model=list[ActivityResponse])
async def list_activities(
    employee_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[ActivityResponse]:
    activities = await EventLogService.list_activities(
        session, employee_id=employee_id, task_id=task_id, limit=limit
    )
    return [ActivityResponse.model_validate(activity) for activity in activities]


@router.get("/alerts", response_model=list[AlertResponse])
async def list_alerts(
    employee_id: str | None = Query(default=None),
    unresolved_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[AlertResponse]:
    alerts = await EventLogService.list_alerts(
        session, employee_id=employee_id, unresolved_only=unresolved_only, limit=limit
    )
    return [AlertResponse.model_validate(alert) for alert in alerts]


@router.get("/events", response_model=list[RealtimeEventResponse])
async def list_events(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[RealtimeEventResponse]:
    events = await EventLogService.list_events(session, after_id=after_id, limit=limit)
    return [RealtimeEventResponse.model_validate(event) for event in events]


@router.get("/events/latest")
async def latest_event_cursor(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    return {"last_event_id": await EventLogService.latest_event_id(session)}


@router.websocket("/ws/events")
async def stream_events(
    websocket: WebSocket,
    after_id: int = Query(default=0, ge=0),
) -> None:
    await websocket.accept()
    cursor = after_id
    try:
        while True:
            async with websocket.app.state.session_factory() as session:
                events = await EventLogService.list_events(session, after_id=cursor, limit=100)
                messages = [RealtimeEventResponse.model_validate(event).model_dump(mode="json") for event in events]
            for message in messages:
                await websocket.send_json(message)
                cursor = message["id"]
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
