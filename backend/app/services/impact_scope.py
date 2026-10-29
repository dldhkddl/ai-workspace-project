from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


IMPACT_SPEC_VERSION = 1


class ImpactScopeError(ValueError):
    pass


def _normalize_path(value: str) -> str:
    raw = str(value).strip().replace("\\", "/").rstrip("/")
    if raw in ("", "."):
        return "."
    if raw.startswith("/") or ":" in raw.split("/")[0]:
        raise ImpactScopeError(f"영향 범위 경로에 절대경로를 쓸 수 없습니다: {value}")
    if any(part in ("", ".", "..") for part in raw.split("/")):
        raise ImpactScopeError(f"영향 범위 경로에 상위 경로 이동을 쓸 수 없습니다: {value}")
    return raw


def normalize_dependency_paths(project_path: str, paths: Any) -> list[str]:
    """공용 의존 경로를 정규화·정렬한다.

    프로젝트 폴더 안의 경로는 이미 프로젝트 트리에 포함되므로 중복이고,
    조용히 버리면 설정 실수를 숨기게 되니 거부한다.
    """
    if paths is None:
        return []
    if not isinstance(paths, (list, tuple)):
        raise ImpactScopeError("의존 경로는 배열이어야 합니다.")

    project = _normalize_path(project_path)
    normalized: list[str] = []
    for path in paths:
        candidate = _normalize_path(path)
        if candidate == ".":
            raise ImpactScopeError("의존 경로로 저장소 전체를 지정할 수 없습니다.")
        if project != "." and (candidate == project or candidate.startswith(f"{project}/")):
            raise ImpactScopeError(f"프로젝트 폴더 안의 경로는 의존 경로가 아닙니다: {candidate}")
        normalized.append(candidate)

    unique = sorted(set(normalized))
    for outer in unique:
        for inner in unique:
            if outer != inner and inner.startswith(f"{outer}/"):
                raise ImpactScopeError(f"의존 경로가 서로 포함됩니다: {outer} ⊃ {inner}")
    return unique


def build_impact_scope(project_path: str, dependency_paths: Any = None) -> dict[str, Any]:
    """승인 레코드에 복사해 둘 '무엇을 검사했는가' 정의."""
    project = _normalize_path(project_path)
    return {
        "version": IMPACT_SPEC_VERSION,
        "project_path": project,
        "dependency_paths": normalize_dependency_paths(project, dependency_paths),
    }


def build_impact_spec(
    scope: dict[str, Any], resolve_object_id: Callable[[str], str | None]
) -> dict[str, Any]:
    """범위 정의에 실제 Git 객체 ID 를 채운 명세.

    경로가 그 커밋에 없으면 object_id 는 null 이다. 경로가 생기거나 사라지는 것도
    영향 범위의 변화이므로 해시가 달라져야 한다.
    """
    return {
        "version": IMPACT_SPEC_VERSION,
        "project": {
            "path": scope["project_path"],
            "object_id": resolve_object_id(scope["project_path"]),
        },
        "dependencies": [
            {"path": path, "object_id": resolve_object_id(path)}
            for path in scope["dependency_paths"]
        ],
    }


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def impact_scope_hash(scope: dict[str, Any]) -> str:
    """범위 '설정'의 해시. 이 값이 바뀌면 기존 승인은 재승인이 아니라 무효다."""
    return _digest(scope)


def impact_hash(spec: dict[str, Any]) -> str:
    """범위 '내용'의 해시. 같으면 승인한 내용 그대로다."""
    return _digest(spec)
