from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


# 설정으로 지울 수 없는 금지 경로. 비공개 에이전트 지침을 승인 대상으로 만들 수 없다.
ALWAYS_FORBIDDEN_PROJECT_NAMES = ("AI-메모리",)


class Settings(BaseSettings):
    service_name: str = "samplebusiness-ai-company"
    version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:54322/postgres?ssl=disable"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    worker_poll_seconds: float = 1
    worker_lease_seconds: float = 60
    worker_heartbeat_seconds: float = 10
    worker_task_timeout_seconds: float = 1200
    manager_ai_runtime: Literal["codex", "openai"] = "codex"
    codex_general_manager_model: str = "gpt-5.6-sol"
    codex_general_manager_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "high"
    codex_project_manager_model: str = "gpt-5.6-sol"
    codex_project_manager_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "medium"
    codex_timeout_seconds: float = 300
    codex_employee_model: str = "gpt-5.6-sol"
    codex_employee_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "medium"
    codex_development_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "high"
    codex_development_timeout_seconds: float = 1200
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    openai_project_manager_model: str = "gpt-5.6-sol"
    openai_project_manager_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "medium"
    openai_general_manager_model: str = "gpt-5.6-sol"
    openai_general_manager_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "high"
    openai_max_output_tokens: int = 1800
    openai_timeout_seconds: float = 120
    agent_memory_root: Path = Path(__file__).resolve().parents[3] / "AI-메모리"
    development_mode_enabled: bool = False
    development_repository_root: Path = Path(__file__).resolve().parents[2]
    development_worktree_root: Path = Path(__file__).resolve().parents[3].parent / ".ai-worktrees"
    development_artifact_root: Path = Path(__file__).resolve().parents[3].parent / ".ai-artifacts"
    development_artifact_retention_days: int = 30
    development_project_path: str = "."
    # 비공개 에이전트 지침 저장소는 승인 대상이 될 수 없다.
    development_forbidden_project_paths: str = ""
    development_commit_author_name: str = "AI 운영센터"
    development_commit_author_email: str = "ai-company@localhost"
    development_patch_preview_limit: int = 200_000
    development_allow_repository_scope: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def forbidden_project_names(self) -> tuple[str, ...]:
        """설정값은 금지 목록에 더하기만 한다. 기본 금지 경로는 지울 수 없다."""
        configured = (name.strip() for name in self.development_forbidden_project_paths.split(","))
        names: list[str] = []
        seen: set[str] = set()
        for name in (*ALWAYS_FORBIDDEN_PROJECT_NAMES, *(n for n in configured if n)):
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                names.append(name)
        return tuple(names)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
