"""Tests for DSL safety verifier and evaluator.

Covers:
- AST safety: __import__('os') rejection
- All forbidden node types
- Whitelisted helpers accepted
- Expression application end-to-end
"""

from __future__ import annotations

import pytest

from open_banca_parsing.dsl.evaluator import EvaluationError, apply_expression
from open_banca_parsing.dsl.safety import DSLSafetyViolation, verify_expression

# ---------------------------------------------------------------------------
# Safety verifier — forbidden constructs
# ---------------------------------------------------------------------------


class TestSafetyVerifier:
    # --- Forbidden: __import__ / builtins
    def test_rejects_import_builtin(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("__import__('os')")

    def test_rejects_dunder_name(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("__builtins__")

    def test_rejects_import_statement(self) -> None:
        with pytest.raises((DSLSafetyViolation, SyntaxError)):
            verify_expression("import os")

    # --- Forbidden: attribute access
    def test_rejects_attribute_access(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("os.system('ls')")

    def test_rejects_chained_attribute(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("''.join(['a','b'])")  # ''.join — Attribute on str

    # --- Forbidden: lambda
    def test_rejects_lambda(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("lambda x: x")

    # --- Forbidden: comprehensions
    def test_rejects_list_comprehension(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("[x for x in range(10)]")

    def test_rejects_dict_comprehension(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("{k: v for k, v in items}")

    # --- Forbidden: subscript
    def test_rejects_subscript(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("d['key']")

    # --- Forbidden: f-string (JoinedStr)
    def test_rejects_fstring(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("f'hello {name}'")

    # --- Forbidden: eval/exec explicitly
    def test_rejects_eval(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("eval('1+1')")

    def test_rejects_exec(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("exec('print(1)')")

    # --- Forbidden: non-whitelisted helper call
    def test_rejects_unknown_helper(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("dangerous_fn('arg')")

    def test_rejects_open_call(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("open('/etc/passwd')")

    # --- Allowed: whitelisted helpers
    def test_accepts_parse_date(self) -> None:
        tree = verify_expression("parse_date('%d/%m/%Y')")
        assert tree is not None

    def test_accepts_trim(self) -> None:
        tree = verify_expression("trim()")
        assert tree is not None

    def test_accepts_extract_regex(self) -> None:
        tree = verify_expression("extract_regex(r'TXN-(\\d+)', 1)")
        assert tree is not None

    def test_accepts_normalize_amount(self) -> None:
        tree = verify_expression("normalize_amount('en_US')")
        assert tree is not None

    def test_accepts_lookup_table_with_dict(self) -> None:
        tree = verify_expression("lookup_table({'DEB': 'debit'})")
        assert tree is not None

    def test_accepts_to_upper(self) -> None:
        tree = verify_expression("to_upper()")
        assert tree is not None

    def test_accepts_concat_with_sep(self) -> None:
        tree = verify_expression("concat(sep='-')")
        assert tree is not None

    # --- Edge cases
    def test_rejects_empty_expression(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(DSLSafetyViolation):
            verify_expression("   ")

    def test_rejects_multiline_injection(self) -> None:
        with pytest.raises((DSLSafetyViolation, SyntaxError)):
            verify_expression("trim()\nexec('evil')")


# ---------------------------------------------------------------------------
# Evaluator — apply_expression
# ---------------------------------------------------------------------------


class TestApplyExpression:
    def test_trim_bare_name(self) -> None:
        result = apply_expression("trim", "  hello  ")
        assert result == "hello"

    def test_to_upper(self) -> None:
        result = apply_expression("to_upper", "hello")
        assert result == "HELLO"

    def test_to_lower(self) -> None:
        result = apply_expression("to_lower", "HELLO")
        assert result == "hello"

    def test_parse_date(self) -> None:
        result = apply_expression("parse_date('%d/%m/%Y')", "15/03/2024")
        assert result.day == 15

    def test_normalize_amount(self) -> None:
        from decimal import Decimal

        result = apply_expression("normalize_amount('en_US')", "1,234.56")
        assert result == Decimal("1234.56")

    def test_extract_regex(self) -> None:
        result = apply_expression("extract_regex(r'TXN-(\\d+)', 1)", "TXN-99999-END")
        assert result == "99999"

    def test_lookup_table(self) -> None:
        result = apply_expression("lookup_table({'DEB': 'debit', 'CRE': 'credit'})", "DEB")
        assert result == "debit"

    def test_import_injection_rejected(self) -> None:
        with pytest.raises((DSLSafetyViolation, EvaluationError)):
            apply_expression("__import__('os')", "ignored")

    def test_attribute_injection_rejected(self) -> None:
        with pytest.raises((DSLSafetyViolation, EvaluationError)):
            apply_expression("os.system('ls')", "ignored")
