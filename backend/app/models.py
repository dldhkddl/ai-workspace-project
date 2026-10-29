from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .services.impact_scope import IMPACT_SPEC_VERSION
from .services.verification_policy import DEFAULT_VERIFICATION_POLICY, verification_policy_hash


CONNECTION_STATUSES = (
    "not_connected", "setup_required", "awaiting_first_sync", "healthy", "delayed", "error", "stopped",
)
WORK_STATUSES = ("idle", "working", "reviewing", "awaiting_approval", "completed", "failed")
TASK_STATUSES = ("queued", "running", "cancel_requested", "cancelled", "completed", "failed")
ALERT_SEVERITIES = ("info", "warning", "error")
PROJECT_STATUSES = ("active", "paused")
APPROVAL_STATUSES = (
    "pending", "approved", "applying", "blocked", "applied",
    "rejected", "invalidated", "expired",
)
# 아직 결론이 나지 않은 승인. 같은 결과에 이 상태가 둘 이상 있으면 안 된다.
# blocked 는 사용자가 원본을 정리하면 다시 진행할 수 있으므로 살아 있는 승인이다.
ACTIVE_APPROVAL_STATUSES = ("pending", "approved", "applying", "blocked")
ACTIVE_APPROVAL_PREDICATE = (
    "status IN ('pending','approved','applying','blocked')"
)

# PostgreSQL 에서는 JSONB, 테스트용 SQLite 에서는 JSON 으로 매핑된다.
JsonColumn = JSON().with_variant(JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    short_name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(30), nullable=False)
    employees: Mapped[list["Employee"]] = relationship(back_populates="team")


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (Index("ix_employees_team_id", "team_id"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(160), nullable=False)
    icon: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    planned_source: Mapped[str] = mapped_column(String(160), nullable=False)
    planned_features: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    team: Mapped[Team] = relationship(back_populates="employees")
    connection: Mapped["EmployeeConnection"] = relationship(back_populates="employee", uselist=False, cascade="all, delete-orphan")
    work: Mapped["EmployeeWorkState"] = relationship(back_populates="employee", uselist=False, cascade="all, delete-orphan")
    tasks: Mapped[list["QueueTask"]] = relationship(back_populates="employee")


class EmployeeConnection(Base):
    __tablename__ = "employee_connections"
    __table_args__ = (CheckConstraint(f"status IN {CONNECTION_STATUSES!r}", name="ck_connection_status"), Index("ix_connections_status", "status"))

    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="not_connected")
    source: Mapped[str | None] = mapped_column(String(160))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSON)
    employee: Mapped[Employee] = relationship(back_populates="connection")


class EmployeeWorkState(Base):
    __tablename__ = "employee_work_states"
    __table_args__ = (CheckConstraint(f"status IS NULL OR status IN {WORK_STATUSES!r}", name="ck_work_status"),)

    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(40))
    task_id: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[str | None] = mapped_column(String(120))
    progress: Mapped[int | None] = mapped_column(Integer)
    employee: Mapped[Employee] = relationship(back_populates="work")


class QueueTask(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(f"status IN {TASK_STATUSES!r}", name="ck_task_status"),
        CheckConstraint("attempt_count >= 0", name="ck_task_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_task_max_attempts"),
        Index("ix_tasks_status_created_at", "status", "created_at"),
        Index("ix_tasks_employee_id", "employee_id"),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    # 분석 작업은 프로젝트에 묶이지 않는다. 개발 작업은 생성 시점에 반드시 묶인다.
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    # PM 계획에서 파생된 직원 작업과 종합 작업을 원래 계획에 묶는다.
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"))
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    input_payload: Mapped[dict] = mapped_column("payload", JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, default=lambda: str(uuid4()))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    employee: Mapped[Employee] = relationship(back_populates="tasks")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_created_at", "created_at"),
        Index("ix_activities_employee_created_at", "employee_id", "created_at"),
        Index("ix_activities_task_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(f"severity IN {ALERT_SEVERITIES!r}", name="ck_alert_severity"),
        Index("ix_alerts_created_at", "created_at"),
        Index("ix_alerts_employee_resolved", "employee_id", "resolved_at"),
        Index("ix_alerts_task_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RealtimeEvent(Base):
    __tablename__ = "realtime_events"
    __table_args__ = (Index("ix_realtime_events_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Project(Base):
    """승인이 묶이는 단위. 저장소 하나 안의 허용된 경로 하나가 프로젝트 하나다."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(f"status IN {PROJECT_STATUSES!r}", name="ck_project_status"),
        UniqueConstraint("repository_root", "project_path", name="uq_projects_repository_path"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    repository_root: Mapped[str] = mapped_column(String(500), nullable=False)
    project_path: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # 프로젝트 폴더 밖이지만 동작에 영향을 주는 경로. 영향 범위 해시에 함께 들어간다.
    dependency_paths: Mapped[list[str]] = mapped_column(JsonColumn, nullable=False, default=list)
    verification_policy: Mapped[dict] = mapped_column(
        JsonColumn, nullable=False, default=lambda: dict(DEFAULT_VERIFICATION_POLICY)
    )
    verification_policy_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
        default=lambda: verification_policy_hash(DEFAULT_VERIFICATION_POLICY),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Approval(Base):
    """승인은 브랜치가 아니라 불변 커밋과 해시에 묶인다."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(f"status IN {APPROVAL_STATUSES!r}", name="ck_approval_status"),
        CheckConstraint("revision >= 1", name="ck_approval_revision"),
        # 원본 HEAD 만 움직여 재승인하는 경우 result_sha 는 그대로다.
        # 승인 시도를 불변 기록으로 남기려면 revision 까지 포함해야 막히지 않는다.
        UniqueConstraint("task_id", "result_sha", "revision", name="uq_approvals_task_result_revision"),
        Index("ix_approvals_project_status", "project_id", "status"),
        Index("ix_approvals_thread_id", "thread_id"),
        # revision 으로 이력은 열어 두되, 살아 있는 승인은 하나로 제한한다.
        # 서비스 잠금만으로는 다른 경로로 들어온 삽입을 막지 못한다.
        Index(
            "uq_approvals_active_result",
            "task_id",
            "result_sha",
            unique=True,
            sqlite_where=text(ACTIVE_APPROVAL_PREDICATE),
            postgresql_where=text(ACTIVE_APPROVAL_PREDICATE),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)
    # 후속 수정이 붙으면 작업은 늘어나도 스레드는 하나다. 화면 묶음 단위.
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)

    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    result_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    result_tree: Mapped[str] = mapped_column(String(40), nullable=False)
    # 프로젝트 하위 트리 + 등록된 공용 의존 경로의 '내용' 해시.
    impact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    impact_hash_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=IMPACT_SPEC_VERSION
    )
    # 프로젝트 설정은 나중에 바뀔 수 있다. 승인 당시 무엇을 검사했는지는 여기 복사해 둔다.
    impact_scope: Mapped[dict] = mapped_column(JsonColumn, nullable=False, default=dict)
    # 범위 '설정'의 해시. 이 값이 달라지면 재승인이 아니라 기존 승인 무효다.
    impact_scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 승인을 누른 시점의 원본 HEAD. 적용 직전에 다시 대조한다.
    target_branch: Mapped[str | None] = mapped_column(String(200))
    target_sha_at_approval: Mapped[str | None] = mapped_column(String(40))
    verification_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    has_sensitive_changes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sensitive_files: Mapped[list[str]] = mapped_column(JsonColumn, nullable=False, default=list)
    patch_path: Mapped[str | None] = mapped_column(String(500))
    patch_sha256: Mapped[str | None] = mapped_column(String(64))

    actor_id: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_sha: Mapped[str | None] = mapped_column(String(40))
    invalidated_reason: Mapped[str | None] = mapped_column(String(120))
    # 적용이 막힌 이유. 사용자가 원본을 정리한 뒤 다시 시도할 때 보여준다.
    last_apply_error: Mapped[str | None] = mapped_column(String(400))
    apply_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
