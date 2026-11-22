"""Focused tests: duration settings normalization (UI/API/worker key parity)."""

import pytest

from api.db.workflow_client import workflow_revision_fingerprint
from api.routes.workflow import _normalize_call_duration_configurations


def test_legacy_duration_is_copied_to_worker_key():
    result = _normalize_call_duration_configurations({"max_call_duration": 90})
    assert result["max_call_duration_seconds"] == 90
    assert result["max_call_duration"] == 90


def test_canonical_only_duration_is_accepted():
    result = _normalize_call_duration_configurations({"max_call_duration_seconds": 60})
    assert result["max_call_duration_seconds"] == 60
    assert result["max_call_duration"] == 60


@pytest.mark.parametrize("value", [0, 29, 3601, "abc", True, 12.5])
def test_invalid_durations_are_rejected(value):
    with pytest.raises(ValueError):
        _normalize_call_duration_configurations({"max_call_duration": value})


def test_missing_duration_keys_are_untouched():
    configs = {"silence_config": {"stage1_seconds": 4}}
    assert _normalize_call_duration_configurations(configs) == configs


def test_float_integral_duration_is_accepted():
    result = _normalize_call_duration_configurations({"max_call_duration": 60.0})
    assert result["max_call_duration_seconds"] == 60


def test_conflicting_duration_values_are_rejected():
    with pytest.raises(ValueError, match="Conflicting"):
        _normalize_call_duration_configurations(
            {"max_call_duration_seconds": 60, "max_call_duration": 600}
        )


def test_matching_duration_values_are_accepted():
    result = _normalize_call_duration_configurations(
        {"max_call_duration_seconds": 60, "max_call_duration": 60}
    )
    assert result["max_call_duration_seconds"] == 60
    assert result["max_call_duration"] == 60


def test_invalid_canonical_is_not_masked_by_valid_legacy():
    with pytest.raises(ValueError):
        _normalize_call_duration_configurations(
            {"max_call_duration_seconds": 0, "max_call_duration": 60}
        )


def test_publish_requires_a_duration():
    with pytest.raises(ValueError, match="required before publishing"):
        _normalize_call_duration_configurations({}, require_value=True)


def test_publish_allows_missing_duration_for_economy_ivr():
    configs = {"product_mode": "economy_ivr"}
    assert _normalize_call_duration_configurations(configs, require_value=True) == configs


def test_publish_upgrades_legacy_only_duration():
    result = _normalize_call_duration_configurations(
        {"max_call_duration": 60}, require_value=True
    )
    assert result["max_call_duration_seconds"] == 60
    assert result["max_call_duration"] == 60


def test_normalization_does_not_mutate_the_input():
    configs = {"max_call_duration": 60}
    result = _normalize_call_duration_configurations(configs)
    assert result is not configs
    assert configs == {"max_call_duration": 60}


def test_workflow_revision_fingerprint_is_stable_and_content_sensitive():
    a = workflow_revision_fingerprint(
        {"nodes": [], "edges": []}, {"max_call_duration_seconds": 60}
    )
    b = workflow_revision_fingerprint(
        {"edges": [], "nodes": []}, {"max_call_duration_seconds": 60}
    )
    c = workflow_revision_fingerprint(
        {"nodes": [], "edges": []}, {"max_call_duration_seconds": 61}
    )
    d = workflow_revision_fingerprint(
        {"nodes": [{"id": "start"}], "edges": []},
        {"max_call_duration_seconds": 60},
    )
    assert a == b
    assert c != a
    assert d != a
