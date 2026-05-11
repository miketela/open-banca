"""Test that parser.json validates against the parsing adapter's ParserSpec schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PARSER_PATH = Path(__file__).parent.parent / "parser.json"

# Valid transform helper names from parser_config.py
_VALID_HELPERS = frozenset(
    {
        "parse_date",
        "extract_regex",
        "normalize_amount",
        "lookup_table",
        "coalesce",
        "trim",
        "to_upper",
        "to_lower",
        "concat",
    }
)

# Accepted account type default values
_VALID_ACCOUNT_TYPES = frozenset({"savings", "checking", "credit_card"})


def test_parser_json_file_exists() -> None:
    """parser.json must be present in the banco_general package."""
    assert _PARSER_PATH.exists(), f"parser.json not found at {_PARSER_PATH}"


def test_parser_json_is_valid_json() -> None:
    """parser.json must be parseable as JSON."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data, dict)


def test_parser_validates_as_parser_spec() -> None:
    """parser.json must pass ParserSpec.from_dict without errors."""
    from open_banca_parsing.parser_config import ParserSpec  # type: ignore[import]

    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    spec = ParserSpec.from_dict(data)
    assert spec.bank == "banco_general"
    assert len(spec.sheets) > 0


def test_parser_has_required_top_level_fields() -> None:
    """Required top-level fields must be present."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    for field in ("version", "bank", "sheets"):
        assert field in data, f"Required field {field!r} missing from parser.json"


def test_parser_bank_id_matches() -> None:
    """bank field must be 'banco_general'."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["bank"] == "banco_general"


def test_parser_has_at_least_three_sheet_specs() -> None:
    """Stub must cover savings, checking, and credit_card account types."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    sheets = data.get("sheets", [])
    assert len(sheets) >= 3, (
        f"Expected >=3 sheet specs (savings, checking, credit_card), got {len(sheets)}"
    )


def test_parser_each_sheet_has_required_fields() -> None:
    """Each sheet spec must have name_pattern, header_row, data_start_row, column_map."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    for i, sheet in enumerate(data.get("sheets", [])):
        for field in ("name_pattern", "header_row", "data_start_row", "column_map"):
            assert field in sheet, f"Sheet[{i}] missing required field {field!r}"


def test_parser_each_sheet_has_date_column() -> None:
    """Each sheet must have a column mapping for 'date'."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    for i, sheet in enumerate(data.get("sheets", [])):
        target_fields = [col.get("target_field") for col in sheet.get("column_map", [])]
        assert "date" in target_fields, f"Sheet[{i}] has no 'date' column mapping"


def test_parser_each_sheet_has_description_column() -> None:
    """Each sheet must have a column mapping for 'description'."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    for i, sheet in enumerate(data.get("sheets", [])):
        target_fields = [col.get("target_field") for col in sheet.get("column_map", [])]
        assert "description" in target_fields, f"Sheet[{i}] has no 'description' column mapping"


def test_parser_all_transform_helpers_are_valid() -> None:
    """Every transform helper referenced must be in the whitelist."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    invalid: list[tuple[int, str, str]] = []
    for i, sheet in enumerate(data.get("sheets", [])):
        for col in sheet.get("column_map", []):
            for transform in col.get("transformations", []):
                helper = transform.get("helper", "")
                if helper and helper not in _VALID_HELPERS:
                    invalid.append((i, col.get("target_field", "?"), helper))
    assert invalid == [], f"Unknown transform helpers: {invalid}"


def test_parser_account_type_inference_defaults_valid() -> None:
    """account_type_inference.default must be a valid account type."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    for i, sheet in enumerate(data.get("sheets", [])):
        ati = sheet.get("account_type_inference", {})
        default_type = ati.get("default", "checking")
        assert default_type in _VALID_ACCOUNT_TYPES, (
            f"Sheet[{i}] account_type_inference.default={default_type!r} is not valid. "
            f"Must be one of {sorted(_VALID_ACCOUNT_TYPES)}"
        )


def test_parser_id_strategy_is_valid() -> None:
    """id_strategy.strategy must be 'embedded' or 'fingerprint'."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    for i, sheet in enumerate(data.get("sheets", [])):
        id_strat = sheet.get("id_strategy", {})
        strategy = id_strat.get("strategy", "fingerprint")
        assert strategy in ("embedded", "fingerprint"), (
            f"Sheet[{i}] id_strategy.strategy={strategy!r} is not valid"
        )


def test_parser_defaults_currency_is_pab() -> None:
    """Default currency should be PAB for Panama."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    defaults = data.get("defaults", {})
    currency = defaults.get("currency", "PAB")
    assert currency == "PAB", f"Expected currency PAB, got {currency!r}"


@pytest.mark.parametrize(
    "account_type",
    ["savings", "checking", "credit_card"],
)
def test_parser_covers_account_type(account_type: str) -> None:
    """The stub parser must have a sheet spec for each account type."""
    raw = _PARSER_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    inferred_types = set()
    for sheet in data.get("sheets", []):
        ati = sheet.get("account_type_inference", {})
        default_type = ati.get("default", "checking")
        inferred_types.add(default_type)
    assert account_type in inferred_types, (
        f"No sheet spec found with account_type_inference.default={account_type!r}"
    )
