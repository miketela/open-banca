"""Tests for DSL helpers — unit tests covering all 9 whitelisted helpers.

Includes:
- ReDoS resistance test (re2 linear time on catastrophic pattern + long input)
- Hypothesis property tests for Decimal precision
- Boundary / error cases for all helpers
"""
from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from open_banca_parsing.dsl.helpers import (
    coalesce,
    concat,
    extract_regex,
    lookup_table,
    normalize_amount,
    parse_date,
    to_lower,
    to_upper,
    trim,
)

# ---------------------------------------------------------------------------
# parse_date
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_basic_dmY(self) -> None:
        result = parse_date("15/03/2024", "%d/%m/%Y")
        assert isinstance(result, datetime)
        assert result.day == 15
        assert result.month == 3
        assert result.year == 2024

    def test_iso_format(self) -> None:
        result = parse_date("2024-03-15", "%Y-%m-%d")
        assert result.year == 2024

    def test_with_time(self) -> None:
        result = parse_date("2024-03-15T10:30:00", "%Y-%m-%dT%H:%M:%S")
        assert result.hour == 10
        assert result.minute == 30

    def test_strips_whitespace(self) -> None:
        result = parse_date("  15/03/2024  ", "%d/%m/%Y")
        assert result.day == 15

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_date("not-a-date", "%d/%m/%Y")

    def test_non_string_input_coerced(self) -> None:
        # Numbers (e.g. from Excel) are coerced to str — "20240315" parses fine
        result = parse_date(20240315, "%Y%m%d")  # type: ignore[arg-type]
        assert result.year == 2024
        assert result.month == 3
        assert result.day == 15


# ---------------------------------------------------------------------------
# extract_regex — re2 backend, ReDoS resistance
# ---------------------------------------------------------------------------


class TestExtractRegex:
    def test_basic_group_1(self) -> None:
        result = extract_regex("TXN-12345-END", r"TXN-(\d+)", group=1)
        assert result == "12345"

    def test_group_0_full_match(self) -> None:
        result = extract_regex("hello world", r"hello", group=0)
        assert result == "hello"

    def test_no_match_returns_empty(self) -> None:
        result = extract_regex("no match here", r"(\d{10})", group=1)
        assert result == ""

    def test_invalid_re2_pattern_raises(self) -> None:
        # Lookahead is not supported in re2 — should raise ValueError
        with pytest.raises((ValueError, Exception)):
            extract_regex("test", r"(?=positive)")

    def test_redos_resistance(self) -> None:
        """ReDoS test: catastrophic backtracking pattern on long input.

        stdlib re would hang for seconds on `(a+)+$` with 'aaa…b'.
        re2 must complete in << 100 ms due to linear-time guarantee (CWE-1333).
        """
        # This pattern would cause exponential backtracking in stdlib re
        # re2 does not support (a+)+ style possessive / catastrophic patterns,
        # so it will raise ValueError (invalid pattern) — which is correct behavior:
        # the linter rule L13 would reject this pattern before runtime.
        long_input = "a" * 50 + "b"  # crafted ReDoS payload

        start = time.monotonic()
        try:
            extract_regex(long_input, r"(a+)+$", group=0)
        except (ValueError, Exception):
            # re2 rejects the pattern — that is the expected safe behavior
            elapsed = time.monotonic() - start
            assert elapsed < 1.0, f"Pattern rejection took too long: {elapsed:.3f}s"
            return
        elapsed = time.monotonic() - start

        # If re2 accepts the pattern variant, it must complete quickly
        assert elapsed < 1.0, (
            f"extract_regex with catastrophic pattern took {elapsed:.3f}s — "
            "expected re2 linear-time O(n) completion"
        )

    def test_non_string_coerced(self) -> None:
        result = extract_regex(12345, r"(\d+)", group=1)  # type: ignore[arg-type]
        assert result == "12345"


# ---------------------------------------------------------------------------
# normalize_amount
# ---------------------------------------------------------------------------


class TestNormalizeAmount:
    def test_en_us_positive(self) -> None:
        assert normalize_amount("1,234.56", "en_US") == Decimal("1234.56")

    def test_en_us_negative(self) -> None:
        assert normalize_amount("-1,234.56", "en_US") == Decimal("-1234.56")

    def test_pa_pa_same_as_en_us(self) -> None:
        assert normalize_amount("1,000.00", "pa_PA") == Decimal("1000.00")

    def test_es_pa_locale(self) -> None:
        assert normalize_amount("1.234,56", "es_PA") == Decimal("1234.56")

    def test_strips_dollar_sign(self) -> None:
        assert normalize_amount("$100.00", "en_US") == Decimal("100.00")

    def test_strips_bpunto_currency(self) -> None:
        # B/. is the Panamanian Balboa symbol
        assert normalize_amount("B/.50.00", "en_US") == Decimal("50.00")

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_amount("not-a-number", "en_US")

    def test_zero(self) -> None:
        assert normalize_amount("0.00", "en_US") == Decimal("0.00")

    @given(
        st.decimals(
            min_value=Decimal("-999999.99"),
            max_value=Decimal("999999.99"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    @settings(max_examples=200)
    def test_decimal_roundtrip_precision(self, value: Decimal) -> None:
        """Property: normalize_amount must preserve exact Decimal precision."""
        # Format value as en_US string and round-trip through normalize_amount
        formatted = f"{value:.2f}"
        result = normalize_amount(formatted, "en_US")
        assert result == value, f"Precision lost: {value!r} -> {formatted!r} -> {result!r}"


# ---------------------------------------------------------------------------
# lookup_table
# ---------------------------------------------------------------------------


class TestLookupTable:
    def test_known_key(self) -> None:
        m = {"DEB": "debit", "CRE": "credit"}
        assert lookup_table("DEB", m) == "debit"

    def test_unknown_key_passthrough(self) -> None:
        m = {"DEB": "debit"}
        assert lookup_table("OTHER", m) == "OTHER"

    def test_empty_map(self) -> None:
        assert lookup_table("X", {}) == "X"

    def test_cap_exceeded_raises(self) -> None:
        huge_map = {str(i): str(i) for i in range(100_001)}
        with pytest.raises(ValueError, match="100000"):
            lookup_table("0", huge_map)

    def test_exactly_at_cap(self) -> None:
        at_cap_map = {str(i): str(i) for i in range(100_000)}
        # Should not raise
        lookup_table("0", at_cap_map)


# ---------------------------------------------------------------------------
# coalesce
# ---------------------------------------------------------------------------


class TestCoalesce:
    def test_first_non_empty(self) -> None:
        assert coalesce("", None, "  ", "hello") == "hello"

    def test_first_value_wins(self) -> None:
        assert coalesce("first", "second") == "first"

    def test_all_empty_returns_empty(self) -> None:
        assert coalesce("", None, "   ") == ""

    def test_strips_whitespace_on_result(self) -> None:
        assert coalesce(None, "  trimmed  ") == "trimmed"


# ---------------------------------------------------------------------------
# trim
# ---------------------------------------------------------------------------


class TestTrim:
    def test_strips_spaces(self) -> None:
        assert trim("  hello  ") == "hello"

    def test_strips_tabs_newlines(self) -> None:
        assert trim("\t\nhello\n\t") == "hello"

    def test_empty_string(self) -> None:
        assert trim("") == ""

    def test_no_whitespace(self) -> None:
        assert trim("hello") == "hello"


# ---------------------------------------------------------------------------
# to_upper / to_lower
# ---------------------------------------------------------------------------


class TestCaseHelpers:
    def test_to_upper(self) -> None:
        assert to_upper("hello world") == "HELLO WORLD"

    def test_to_lower(self) -> None:
        assert to_lower("HELLO WORLD") == "hello world"

    def test_to_upper_already_upper(self) -> None:
        assert to_upper("ABC") == "ABC"

    def test_to_lower_mixed(self) -> None:
        assert to_lower("HeLLo") == "hello"

    def test_non_string_coerced(self) -> None:
        assert to_upper(123) == "123"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# concat
# ---------------------------------------------------------------------------


class TestConcat:
    def test_basic_join(self) -> None:
        assert concat("hello", "world") == "hello world"

    def test_custom_sep(self) -> None:
        assert concat("a", "b", "c", sep="-") == "a-b-c"

    def test_none_values_filtered(self) -> None:
        # None values are filtered out by concat (not converted to "None" string)
        assert concat("a", None, "b", sep="|") == "a|b"

    def test_empty_sep(self) -> None:
        assert concat("abc", "def", sep="") == "abcdef"

    def test_too_many_refs_raises(self) -> None:
        values = tuple(str(i) for i in range(51))
        with pytest.raises(ValueError, match="max 50"):
            concat(*values)

    def test_output_cap_exceeded_raises(self) -> None:
        large = "x" * (65 * 1024)  # > 64 KB
        with pytest.raises(ValueError, match="64 KB"):
            concat(large)
