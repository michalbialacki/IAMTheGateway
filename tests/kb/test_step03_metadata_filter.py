"""Tests for Phase 05 Step 03 – metadataFilter builder.

Verifies that build_metadata_filter produces the exact structure required by
Bedrock Knowledge Base RetrieveAndGenerate vectorSearchConfiguration.filter:

  {
    "andAll": [
      { "equals":           { "key": "department",     "value": <str> } },
      { "lessThanOrEquals": { "key": "clearance_level","value": <int> } },
    ]
  }

All tests are local — no AWS calls.
"""

import importlib.util
import json
from pathlib import Path

import pytest

# ─── Load handler module ──────────────────────────────────────────────────────

_HANDLER_PATH = Path(__file__).resolve().parents[2] / "lambda" / "sts" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


handler = _load_handler()
build_metadata_filter = handler.build_metadata_filter


# ─── Structure tests ──────────────────────────────────────────────────────────


class TestMetadataFilterStructure:
    def test_top_level_key_is_andAll(self):
        f = build_metadata_filter("alpha", 1)
        assert list(f.keys()) == ["andAll"]

    def test_andAll_has_two_conditions(self):
        f = build_metadata_filter("alpha", 1)
        assert len(f["andAll"]) == 2

    def test_no_orAll_at_top_level(self):
        f = build_metadata_filter("alpha", 1)
        assert "orAll" not in f

    def test_serializable_to_json(self):
        f = build_metadata_filter("bravo", 3)
        dumped = json.dumps(f)
        assert json.loads(dumped) == f


# ─── Department filter (equals) ───────────────────────────────────────────────


class TestDepartmentCondition:
    def _dept_condition(self, dept: str, cl: int = 0) -> dict:
        return build_metadata_filter(dept, cl)["andAll"][0]

    def test_uses_equals_operator(self):
        cond = self._dept_condition("alpha")
        assert "equals" in cond

    def test_key_is_department(self):
        cond = self._dept_condition("alpha")
        assert cond["equals"]["key"] == "department"

    def test_value_matches_input(self):
        cond = self._dept_condition("bravo")
        assert cond["equals"]["value"] == "bravo"

    def test_value_is_string(self):
        cond = self._dept_condition("alpha")
        assert isinstance(cond["equals"]["value"], str)

    def test_no_extra_keys_in_equals(self):
        cond = self._dept_condition("alpha")
        assert set(cond["equals"].keys()) == {"key", "value"}


# ─── Clearance level filter (lessThanOrEquals) ────────────────────────────────


class TestClearanceLevelCondition:
    def _cl_condition(self, cl: int, dept: str = "alpha") -> dict:
        return build_metadata_filter(dept, cl)["andAll"][1]

    def test_uses_lessThanOrEquals_operator(self):
        cond = self._cl_condition(2)
        assert "lessThanOrEquals" in cond

    def test_key_is_clearance_level(self):
        cond = self._cl_condition(2)
        assert cond["lessThanOrEquals"]["key"] == "clearance_level"

    def test_value_matches_input(self):
        for level in [0, 1, 2, 3, 4]:
            cond = self._cl_condition(level)
            assert cond["lessThanOrEquals"]["value"] == level, f"failed at level={level}"

    def test_value_is_int_not_str(self):
        for level in [0, 1, 2, 3, 4]:
            cond = self._cl_condition(level)
            assert isinstance(cond["lessThanOrEquals"]["value"], int), (
                f"clearance_level must be int, not str at level={level}"
            )

    def test_no_extra_keys_in_lessThanOrEquals(self):
        cond = self._cl_condition(1)
        assert set(cond["lessThanOrEquals"].keys()) == {"key", "value"}


# ─── Cross-department isolation guarantee ─────────────────────────────────────


class TestIsolationGuarantee:
    """Filters for different departments must differ — cross-tenant cannot share a filter."""

    def test_alpha_and_bravo_filters_differ(self):
        f_alpha = build_metadata_filter("alpha", 1)
        f_bravo = build_metadata_filter("bravo", 1)
        assert f_alpha != f_bravo

    def test_same_dept_different_clearance_filters_differ(self):
        f_low  = build_metadata_filter("alpha", 0)
        f_high = build_metadata_filter("alpha", 3)
        assert f_low != f_high

    def test_same_inputs_produce_equal_filters(self):
        f1 = build_metadata_filter("alpha", 2)
        f2 = build_metadata_filter("alpha", 2)
        assert f1 == f2


# ─── All clearance levels produce valid filters ───────────────────────────────


@pytest.mark.parametrize("cl", [0, 1, 2, 3, 4])
def test_valid_filter_for_all_clearance_levels(cl: int):
    f = build_metadata_filter("alpha", cl)
    assert f["andAll"][1]["lessThanOrEquals"]["value"] == cl


@pytest.mark.parametrize("dept", ["alpha", "bravo", "engineering", "hr"])
def test_valid_filter_for_various_departments(dept: str):
    f = build_metadata_filter(dept, 1)
    assert f["andAll"][0]["equals"]["value"] == dept
