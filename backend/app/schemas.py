import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ConnectionStatus = Literal["not_connected", "setup_required", "awaiting_first_sync", "healthy", "delayed", "error", "stopped"]
WorkStatus = Literal["idle", "working", "reviewing", "awaiting_approval", "completed", "failed"] | None
TaskStatus = Literal["queued", "running", "cancel_requested", "cancelled", "completed", "failed"]
AlertSeverity = Literal["info", "warning", "error"]


class ErrorInfo(BaseModel):
    code: str
    message: str


class ConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: ConnectionStatus
    source: str | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    error: ErrorInfo | None = None


class WorkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: WorkStatus = None
    task_id: str | None = None
    title: str | None = None
    stage: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    team_id: str
    role: str
    icon: str
    summary: str | None = None
    allowed_tools: list[str]
    approval_required: bool
    planned_source: str
    planned_features: list[str]
    connection: ConnectionResponse
    work: WorkResponse
    alerts: list[str] = []


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    short_name: str
    color: str
    employee_ids: list[str]


class OfficeResponse(BaseModel):
    teams: list[TeamResponse]
    employees: list[EmployeeResponse]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    employee_id: str = Field(min_length=1, max_length=120)
    task_type: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def validate_payload_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except ValueError as exc:
            raise ValueError("payload contains a non-finite number") from exc
        if len(serialized.encode("utf-8")) > 32_768:
            raise ValueError("payload is too large")
        return value


class TaskSummaryResponse(BaseModel):
    id: str
    employee_id: str
    task_type: str
    title: str
    status: TaskStatus
    correlation_id: str
    attempt_count: int
    max_attempts: int
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    requires_user_response: bool = False


class TaskDetailResponse(TaskSummaryResponse):
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: ErrorInfo | None = None
    will_retry: bool = False


ProjectWorkflowStatus = Literal[
    "awaiting_user", "replanning", "continued",
    "not_started", "running", "discussing", "synthesizing", "completed", "failed",
]


class ProjectWorkflowResponse(BaseModel):
    status: ProjectWorkflowStatus
    plan_task_id: str
    assignments: list[TaskSummaryResponse]
    discussions: list[TaskSummaryResponse]
    synthesis: TaskDetailResponse | None = None
    continuation: TaskDetailResponse | None = None


class ProjectPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["approve", "revise"]
    message: str | None = Field(default=None, max_length=8_000)

    @model_validator(mode="after")
    def require_revision_message(self) -> "ProjectPlanResponse":
        if self.action == "revise" and not self.message:
            raise ValueError("수정 요청 내용을 입력해 주세요.")
        return self


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str | None
    task_id: str | None
    event_type: str
    title: str
    detail: dict[str, Any]
    correlation_id: str | None
    created_at: datetime


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str | None
    task_id: str | None
    severity: AlertSeverity
    code: str
    message: str
    detail: dict[str, Any]
    correlation_id: str | None
    resolved_at: datetime | None
    created_at: datetime


class RealtimeEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    resource_type: str
    resource_id: str
    payload: dict[str, Any]
    correlation_id: str | None
    created_at: datetime


ApprovalStatus = Literal[
    "pending", "approved", "applying", "blocked", "applied",
    "rejected", "invalidated", "expired",
]


class ApprovalSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    task_id: str
    thread_id: str
    branch: str
    status: ApprovalStatus
    revision: int
    result_sha: str
    has_sensitive_changes: bool
    expires_at: datetime | None
    applied_at: datetime | None
    created_at: datetime


class ApprovalDetailResponse(ApprovalSummaryResponse):
    base_sha: str
    result_tree: str
    impact_hash: str
    target_branch: str | None
    target_sha_at_approval: str | None
    applied_sha: str | None
    invalidated_reason: str | None
    last_apply_error: str | None
    apply_attempt: int
    actor_id: str | None
    sensitive_files: list[str] = []
    # 아래는 작업 결과에서 함께 실어 보낸다. 화면이 승인 전에 내용을 볼 수 있어야 한다.
    task_title: str | None = None
    employee_id: str | None = None
    changed_files: list[str] = []
    secret_scan_skipped_files: list[str] = []
    diff_stat: str | None = None
    patch_preview: str | None = None
    patch_preview_truncated: bool = False


class ApprovalDecision(BaseModel):
    """승인 대상 브랜치와 HEAD 는 요청에 담지 않는다. 서버가 Git 에서 직접 읽는다."""

    model_config = ConfigDict(extra="forbid")

    result_sha: str = Field(min_length=7, max_length=40)
    revision: int = Field(ge=1)
    acknowledge_sensitive: bool = False
