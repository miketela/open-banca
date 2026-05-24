"""Test that map.json validates against the BankMap domain entity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_banca_domain.entities.bank_map import BankMap

_MAP_PATH = Path(__file__).parent.parent / "map.json"

# Known valid actions from step_dispatcher.STEP_DISPATCH_TABLE
_KNOWN_ACTIONS = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "wait_for_selector",
        "wait_for_download",
        "select_date_range",
        "assert_text",
        "extract_table",
        "download_file",
        "prompt_user",
    }
)


def test_map_json_file_exists() -> None:
    """map.json must be present in the banco_general package."""
    assert _MAP_PATH.exists(), f"map.json not found at {_MAP_PATH}"


def test_map_json_is_valid_json() -> None:
    """map.json must be parseable as JSON."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)  # raises json.JSONDecodeError on failure
    assert isinstance(data, dict)


def test_map_json_validates_as_bank_map() -> None:
    """map.json must pass BankMap.model_validate without errors."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    bank_map = BankMap.model_validate(data)
    assert bank_map.bank_id == "banco_general"
    assert bank_map.schema_version is not None
    assert bank_map.version is not None
    assert len(bank_map.steps) > 0


def test_map_bank_id_matches_package() -> None:
    """bank_id in map.json must be 'banco_general'."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["bank_id"] == "banco_general"


def test_map_has_minimum_required_steps() -> None:
    """The stub map must have at least login + one account download flow."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    bank_map = BankMap.model_validate(data)

    actions = [s.action for s in bank_map.steps]
    # Must have navigate, fill, click (login flow)
    assert "navigate" in actions, "No navigate step found"
    assert "fill" in actions, "No fill step found"
    assert "click" in actions, "No click step found"
    # Must have at least one download step
    assert "download_file" in actions, "No download_file step found"


def test_all_step_actions_are_known() -> None:
    """Every step.action must be in the known dispatch table."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    bank_map = BankMap.model_validate(data)

    unknown = [(s.step_id, s.action) for s in bank_map.steps if s.action not in _KNOWN_ACTIONS]
    assert unknown == [], f"Unknown actions: {unknown}"


def test_fill_steps_have_value_ref() -> None:
    """All fill steps must have value_ref (not embedded literal credentials)."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    bank_map = BankMap.model_validate(data)

    bad_fills = [
        s.step_id
        for s in bank_map.steps
        if s.action == "fill" and not s.model_dump(mode="json").get("value_ref", "")
    ]
    assert bad_fills == [], f"Fill steps missing value_ref: {bad_fills}"


def test_sensitive_fill_steps_have_sensitive_true() -> None:
    """Steps that fill passwords must be marked sensitive=True."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    bank_map = BankMap.model_validate(data)

    for step in bank_map.steps:
        if step.action == "fill":
            step_data = step.model_dump(mode="json")
            value_ref: str = step_data.get("value_ref", "")
            # Password steps must be marked sensitive
            if "password" in value_ref.lower() or "clave" in value_ref.lower():
                sensitive = step_data.get("sensitive", False)
                assert sensitive is True, (
                    f"Fill step {step.step_id!r} references password credential but sensitive=False"
                )


def test_navigate_before_fill() -> None:
    """A navigate step must appear before any fill step (login form cannot be filled without navigation)."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    bank_map = BankMap.model_validate(data)

    seen_navigate = False
    for step in bank_map.steps:
        if step.action == "navigate":
            seen_navigate = True
        if step.action == "fill":
            assert seen_navigate, f"Fill step {step.step_id!r} appears before any navigate step"
            break


def test_step_ids_are_unique() -> None:
    """All step_ids must be unique within the map."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    bank_map = BankMap.model_validate(data)

    step_ids = [s.step_id for s in bank_map.steps]
    assert len(step_ids) == len(set(step_ids)), f"Duplicate step_ids found: {step_ids}"


@pytest.mark.parametrize("field", ["bank_id", "version", "schema_version", "steps"])
def test_required_top_level_fields_present(field: str) -> None:
    """Required top-level fields must be present in map.json."""
    raw = _MAP_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert field in data, f"Required field {field!r} missing from map.json"
