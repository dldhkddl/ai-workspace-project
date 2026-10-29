from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import SessionLocal, get_session
from .api.routes import router as office_router
from .api.task_routes import router as task_router
from .api.approval_routes import router as approval_router
from .api.event_routes import router as event_router
from .services.project_registry import register_configured_project


def create_app() -> FastAPI:
    settings = get_settings()
    @asynccontextmanager
    async def lifespan(instance: FastAPI):
        async with instance.state.session_factory() as session:
            await register_configured_project(session, settings)
        yield

    app = FastAPI(title="샘플업체 AI 운영센터 API", version=settings.version, lifespan=lifespan)
    app.state.session_factory = SessionLocal
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}},
        )

    api = APIRouter(prefix="/api/v1")

    @api.get("/health", response_model=None)
    async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str] | JSONResponse:
        try:
            await session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={"error": {"code": "DATABASE_UNAVAILABLE", "message": "데이터베이스를 사용할 수 없습니다."}},
            )
        return {
            "service": settings.service_name,
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    app.include_router(api)
    app.include_router(office_router, prefix="/api/v1")
    app.include_router(task_router, prefix="/api/v1")
    app.include_router(approval_router, prefix="/api/v1")
    app.include_router(event_router, prefix="/api/v1")
    return app


app = create_app()
