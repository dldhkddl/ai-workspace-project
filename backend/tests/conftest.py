import sys
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.db import get_session
from app.config import get_settings
from app.main import create_app
from app.models import Base
from app.seed import seed_database


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 테스트 앱이 개발자의 실제 로컬 저장소를 자동 등록하지 않도록 격리한다.
    monkeypatch.setenv("DEVELOPMENT_MODE_ENABLED", "false")
    monkeypatch.setenv("DEVELOPMENT_REPOSITORY_ROOT", str(tmp_path / "등록하지-않는-저장소"))
    monkeypatch.setenv("DEVELOPMENT_PROJECT_PATH", "시험-프로젝트")
    monkeypatch.setenv("DEVELOPMENT_ALLOW_REPOSITORY_SCOPE", "false")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            await seed_database(session)

    async def override_session():
        async with factory() as session:
            yield session

    asyncio.run(setup())
    app = create_app()
    app.state.session_factory = factory
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(engine.dispose())
    get_settings.cache_clear()
