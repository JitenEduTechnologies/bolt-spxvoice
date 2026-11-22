"""Focused tests: usage idempotency claim classification."""

from types import SimpleNamespace

from api.db.organization_usage_client import _is_duplicate_idempotency_claim


class _FakeOrig:
    def __init__(self, sqlstate: str, constraint: str = "") -> None:
        self.sqlstate = sqlstate
        self.constraint_name = constraint

    def __str__(self) -> str:
        return f"{self.sqlstate} {self.constraint_name}"


def _error(sqlstate: str, constraint: str = ""):
    return SimpleNamespace(orig=_FakeOrig(sqlstate, constraint))


def test_idempotency_key_unique_violation_is_a_duplicate_claim():
    exc = _error("23505", "action_idempotency_records_idempotency_key_key")
    assert _is_duplicate_idempotency_claim(exc) is True


def test_unrelated_unique_violation_is_not_a_duplicate_claim():
    exc = _error(
        "23505", "organization_usage_cycles_organization_id_period_start_key"
    )
    assert _is_duplicate_idempotency_claim(exc) is False


def test_foreign_key_violation_is_not_a_duplicate_claim():
    assert _is_duplicate_idempotency_claim(_error("23503")) is False


def test_missing_original_exception_is_not_a_duplicate_claim():
    assert _is_duplicate_idempotency_claim(SimpleNamespace(orig=None)) is False
