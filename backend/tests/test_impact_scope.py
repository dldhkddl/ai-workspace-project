import pytest

from app.services.impact_scope import (
    IMPACT_SPEC_VERSION,
    ImpactScopeError,
    build_impact_scope,
    build_impact_spec,
    canonical_json,
    impact_hash,
    impact_scope_hash,
    normalize_dependency_paths,
)


def resolver(mapping: dict[str, str]):
    return lambda path: mapping.get(path)


def test_scope_is_versioned_and_normalized():
    scope = build_impact_scope("AI-company/", ["공용\\라이브러리/", "설정/tsconfig.base.json"])

    assert scope == {
        "version": 1,
        "project_path": "AI-company",
        "dependency_paths": ["공용/라이브러리", "설정/tsconfig.base.json"],
    }


def test_dependency_paths_reject_unsafe_or_redundant_entries():
    for bad in (
        ["/etc"],
        ["C:/Users"],
        ["../밖"],
        ["."],
        ["AI-company"],              # 프로젝트 폴더 자체
        ["AI-company/src"],          # 이미 프로젝트 트리에 포함
        ["공용", "공용/하위"],        # 서로 포함
    ):
        with pytest.raises(ImpactScopeError):
            normalize_dependency_paths("AI-company", bad)

    assert normalize_dependency_paths("AI-company", None) == []
    assert normalize_dependency_paths("AI-company", ["나", "가", "나"]) == ["가", "나"]


def test_impact_hash_is_not_the_same_value_as_the_project_tree():
    scope = build_impact_scope("AI-company")
    tree = "a" * 40
    spec = build_impact_spec(scope, resolver({"AI-company": tree}))

    assert spec == {
        "version": IMPACT_SPEC_VERSION,
        "project": {"path": "AI-company", "object_id": tree},
        "dependencies": [],
    }
    # 의존 경로가 비어도 result_tree 를 그대로 다시 해시하는 것과는 다른 값이어야 한다.
    assert impact_hash(spec) != tree
    assert len(impact_hash(spec)) == 64


def test_missing_dependency_path_is_recorded_as_null_and_changes_the_hash():
    scope = build_impact_scope("AI-company", ["공용"])
    present = build_impact_spec(scope, resolver({"AI-company": "a" * 40, "공용": "b" * 40}))
    absent = build_impact_spec(scope, resolver({"AI-company": "a" * 40}))

    assert absent["dependencies"] == [{"path": "공용", "object_id": None}]
    assert impact_hash(present) != impact_hash(absent)
    assert '"object_id":null' in canonical_json(absent)


def test_dependency_content_change_moves_the_hash_even_when_the_project_tree_is_identical():
    scope = build_impact_scope("AI-company", ["공용"])
    before = build_impact_spec(scope, resolver({"AI-company": "a" * 40, "공용": "b" * 40}))
    after = build_impact_spec(scope, resolver({"AI-company": "a" * 40, "공용": "c" * 40}))

    assert before["project"] == after["project"]
    assert impact_hash(before) != impact_hash(after)


def test_scope_hash_separates_a_settings_change_from_a_content_change():
    narrow = build_impact_scope("AI-company")
    wide = build_impact_scope("AI-company", ["공용"])

    # 설정이 달라지면 범위 해시가 달라진다 → 재승인이 아니라 기존 승인 무효.
    assert impact_scope_hash(narrow) != impact_scope_hash(wide)
    # 같은 설정이면 키 순서와 무관하게 같은 해시.
    assert impact_scope_hash(build_impact_scope("AI-company/")) == impact_scope_hash(narrow)
