import pytest

from app.services.verification_policy import (
    DEFAULT_VERIFICATION_POLICY,
    VerificationPolicyError,
    canonical_policy_json,
    validate_verification_policy,
    verification_policy_hash,
)


ENABLED_POLICY = {
    "version": 1,
    "enabled": True,
    "runner": "container",
    "argv": ["npm", "run", "build"],
    "working_directory": ".",
    "timeout_seconds": 600,
    "network": "none",
}


def test_default_policy_is_disabled_and_minimal():
    assert DEFAULT_VERIFICATION_POLICY == {"version": 1, "enabled": False}
    assert validate_verification_policy(DEFAULT_VERIFICATION_POLICY) == DEFAULT_VERIFICATION_POLICY


def test_hash_ignores_key_order_and_whitespace():
    shuffled = {"enabled": False, "version": 1}
    assert verification_policy_hash(shuffled) == verification_policy_hash(DEFAULT_VERIFICATION_POLICY)
    assert canonical_policy_json(shuffled) == '{"enabled":false,"version":1}'
    assert len(verification_policy_hash(ENABLED_POLICY)) == 64
    assert verification_policy_hash(ENABLED_POLICY) != verification_policy_hash(DEFAULT_VERIFICATION_POLICY)


def test_shell_string_commands_are_refused():
    # verify_command 같은 셸 문자열이 다시 들어오지 못하게 막는다.
    for bad in (
        {"version": 1, "enabled": True, "runner": "container", "network": "none",
         "argv": "npm run build && curl http://x"},
        {"version": 1, "enabled": True, "runner": "shell", "network": "none", "argv": ["npm"]},
        {"version": 1, "enabled": True, "runner": "container", "network": "none",
         "argv": ["npm"], "shell": True},
        {"version": 1, "enabled": False, "verify_command": "npm test"},
    ):
        with pytest.raises(VerificationPolicyError):
            validate_verification_policy(bad)


def test_enabled_policy_is_normalized():
    normalized = validate_verification_policy({**ENABLED_POLICY, "working_directory": "backend/"})

    assert normalized["working_directory"] == "backend"
    assert normalized["argv"] == ["npm", "run", "build"]
    assert normalized["timeout_seconds"] == 600


def test_enabled_policy_rejects_unsafe_values():
    for bad in (
        {**ENABLED_POLICY, "working_directory": "/etc"},
        {**ENABLED_POLICY, "working_directory": "../밖"},
        {**ENABLED_POLICY, "network": "full"},
        {**ENABLED_POLICY, "argv": []},
        {**ENABLED_POLICY, "argv": ["npm", ""]},
        {**ENABLED_POLICY, "timeout_seconds": 0},
        {**ENABLED_POLICY, "timeout_seconds": 4000},
        {**ENABLED_POLICY, "timeout_seconds": True},
        {**ENABLED_POLICY, "version": 2},
        "정책 아님",
    ):
        with pytest.raises(VerificationPolicyError):
            validate_verification_policy(bad)


def test_omitted_defaults_hash_the_same_as_explicit_defaults():
    """기본값을 생략한 정책과 명시한 정책은 같은 정책이므로 해시도 같아야 한다.

    입력을 그대로 해시하면 같은 정책인데 승인이 무효 처리된다.
    """
    omitted = {
        "version": 1, "enabled": True, "runner": "container",
        "argv": ["npm", "run", "build"], "network": "none",
    }

    assert verification_policy_hash(omitted) == verification_policy_hash(ENABLED_POLICY)
    assert validate_verification_policy(omitted) == ENABLED_POLICY


def test_hashing_an_invalid_policy_fails_instead_of_producing_a_value():
    with pytest.raises(VerificationPolicyError):
        verification_policy_hash({"version": 1, "enabled": True, "runner": "shell", "argv": ["x"]})
