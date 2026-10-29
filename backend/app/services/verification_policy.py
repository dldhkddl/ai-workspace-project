from __future__ import annotations

import hashlib
import json
from typing import Any


# 1차에서는 검증을 실행하지 않는다. 자리와 해시만 만들어 두고 2차에서 켠다.
DEFAULT_VERIFICATION_POLICY: dict[str, Any] = {"version": 1, "enabled": False}

ALLOWED_RUNNERS = ("container",)
ALLOWED_NETWORKS = ("none",)
DISABLED_KEYS = frozenset({"version", "enabled"})
ENABLED_KEYS = frozenset({
    "version", "enabled", "runner", "argv", "working_directory", "timeout_seconds", "network",
})


class VerificationPolicyError(ValueError):
    pass


def validate_verification_policy(policy: Any) -> dict[str, Any]:
    """검증 정책을 구조로만 받는다.

    셸 문자열(`verify_command`)을 허용하면 승인 화면이 임의 명령 실행 통로가 되고,
    Windows와 Linux의 해석도 달라진다. 명령은 항상 argv 배열로만 표현한다.
    """
    if not isinstance(policy, dict):
        raise VerificationPolicyError("검증 정책은 객체여야 합니다.")
    if policy.get("version") != 1:
        raise VerificationPolicyError("지원하지 않는 검증 정책 버전입니다.")
    enabled = policy.get("enabled")
    if not isinstance(enabled, bool):
        raise VerificationPolicyError("enabled 는 참·거짓이어야 합니다.")

    allowed = ENABLED_KEYS if enabled else DISABLED_KEYS
    unknown = set(policy) - allowed
    if unknown:
        raise VerificationPolicyError(f"허용하지 않는 검증 정책 항목입니다: {sorted(unknown)}")
    if not enabled:
        return {"version": 1, "enabled": False}

    if policy.get("runner") not in ALLOWED_RUNNERS:
        raise VerificationPolicyError(f"runner 는 {ALLOWED_RUNNERS} 중 하나여야 합니다.")
    if policy.get("network") not in ALLOWED_NETWORKS:
        raise VerificationPolicyError(f"network 는 {ALLOWED_NETWORKS} 중 하나여야 합니다.")

    argv = policy.get("argv")
    if not isinstance(argv, list) or not argv:
        raise VerificationPolicyError("argv 는 비어 있지 않은 배열이어야 합니다.")
    if not all(isinstance(item, str) and item.strip() for item in argv):
        raise VerificationPolicyError("argv 항목은 빈 문자열이 아닌 문자열이어야 합니다.")

    working_directory = policy.get("working_directory", ".")
    if not isinstance(working_directory, str):
        raise VerificationPolicyError("working_directory 는 문자열이어야 합니다.")
    normalized = working_directory.strip().replace("\\", "/").rstrip("/") or "."
    if normalized != ".":
        if normalized.startswith("/") or ":" in normalized.split("/")[0]:
            raise VerificationPolicyError("working_directory 에 절대경로를 쓸 수 없습니다.")
        if any(part in ("", ".", "..") for part in normalized.split("/")):
            raise VerificationPolicyError("working_directory 에 상위 경로 이동을 쓸 수 없습니다.")

    timeout_seconds = policy.get("timeout_seconds", 600)
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise VerificationPolicyError("timeout_seconds 는 정수여야 합니다.")
    if not 1 <= timeout_seconds <= 3600:
        raise VerificationPolicyError("timeout_seconds 는 1초에서 3600초 사이여야 합니다.")

    return {
        "version": 1,
        "enabled": True,
        "runner": policy["runner"],
        "argv": list(argv),
        "working_directory": normalized,
        "timeout_seconds": timeout_seconds,
        "network": policy["network"],
    }


def canonical_policy_json(policy: dict[str, Any]) -> str:
    return json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verification_policy_hash(policy: dict[str, Any]) -> str:
    """검증을 거친 정규형을 해시한다.

    입력을 그대로 해시하면 기본값을 생략한 정책과 명시한 정책이 다른 해시가 나와서,
    같은 정책인데 승인이 무효 처리된다. 항상 validate 를 먼저 통과시킨다.
    """
    normalized = validate_verification_policy(policy)
    return hashlib.sha256(canonical_policy_json(normalized).encode("utf-8")).hexdigest()
