"""dsl/evaluator.py — mini-AST evaluator for whitelisted DSL helper calls.

Dispatches validated AST nodes to the corresponding helper functions.
NO eval/exec/compile/__import__ used anywhere in this module.

The evaluator operates on AST nodes produced by safety.verify_expression(),
which has already guaranteed the tree contains only whitelisted helpers
with constant arguments.
"""
from __future__ import annotations

import ast
from typing import Any

from open_banca_parsing.dsl import helpers as _h
from open_banca_parsing.dsl.safety import ALLOWED_HELPERS, DSLSafetyViolation, verify_expression


class EvaluationError(ValueError):
    """Raised when a DSL expression cannot be evaluated."""


def _eval_constant(node: ast.Constant) -> Any:
    """Extract the Python value from an ast.Constant node."""
    return node.value


def _eval_node(node: ast.expr) -> Any:
    """Recursively evaluate an expression node (constants only at argument positions)."""
    if isinstance(node, ast.Constant):
        return _eval_constant(node)
    if isinstance(node, ast.List):
        return [_eval_node(el) for el in node.elts]
    if isinstance(node, ast.Dict):
        return {_eval_node(k): _eval_node(v) for k, v in zip(node.keys, node.values, strict=False) if k is not None}
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(el) for el in node.elts)
    msg = f"Evaluator: unexpected node type {type(node).__name__!r} — safety check should have caught this."
    raise EvaluationError(msg)


# ---------------------------------------------------------------------------
# Helper dispatch table — maps helper name to its Python function
# ---------------------------------------------------------------------------

_HELPER_MAP: dict[str, Any] = {
    "parse_date": _h.parse_date,
    "extract_regex": _h.extract_regex,
    "normalize_amount": _h.normalize_amount,
    "lookup_table": _h.lookup_table,
    "coalesce": _h.coalesce,
    "trim": _h.trim,
    "to_upper": _h.to_upper,
    "to_lower": _h.to_lower,
    "concat": _h.concat,
}

assert set(_HELPER_MAP.keys()) == ALLOWED_HELPERS, (
    "HELPER_MAP and ALLOWED_HELPERS are out of sync — update both together."
)


def evaluate_call(node: ast.Call) -> tuple[str, list[Any], dict[str, Any]]:
    """Extract (helper_name, args, kwargs) from a validated ast.Call node.

    Does NOT call the helper — returns the components for the engine to
    decide whether to apply it to a cell value.

    Args:
        node: A validated ast.Call node where node.func is an ast.Name
              matching a whitelisted helper.

    Returns:
        Tuple of (helper_name, positional_args, keyword_args).

    Raises:
        EvaluationError: If the node is malformed.
        DSLSafetyViolation: If the helper name is not whitelisted.
    """
    if not isinstance(node.func, ast.Name):
        msg = f"evaluate_call: expected ast.Name, got {type(node.func).__name__!r}"
        raise EvaluationError(msg)
    name = node.func.id
    if name not in ALLOWED_HELPERS:
        msg = f"evaluate_call: {name!r} is not a whitelisted helper"
        raise DSLSafetyViolation(msg)

    args = [_eval_node(arg) for arg in node.args]
    kwargs = {kw.arg: _eval_node(kw.value) for kw in node.keywords if kw.arg is not None}
    return name, args, kwargs


def apply_expression(source: str, cell_value: str) -> Any:
    """Parse, safety-check, and apply a DSL expression to *cell_value*.

    The expression is expected to be a single helper call whose first positional
    argument is implicit (the cell value). Alternatively, the expression string
    may omit the cell value and receive it as the first positional argument.

    Convention: the engine passes ``cell_value`` as the first argument always,
    followed by any arguments declared in the expression string.

    Example::

        apply_expression("parse_date('%d/%m/%Y')", "15/03/2024")
        # internally calls: parse_date("15/03/2024", "%d/%m/%Y")

        apply_expression("trim", "  hello  ")
        # internally calls: trim("  hello  ")

    Args:
        source: DSL expression string, e.g. ``"parse_date('%d/%m/%Y')"`` or ``"trim"``.
        cell_value: The cell value to transform.

    Returns:
        Transformed value (type depends on helper).

    Raises:
        EvaluationError: On evaluation failure.
        DSLSafetyViolation: On safety violation.
    """
    src = source.strip()

    # Allow bare helper names (no call parens) for zero-argument helpers
    if src in ALLOWED_HELPERS and "(" not in src:
        src = f"{src}()"

    tree = verify_expression(src)

    if not isinstance(tree.body, ast.Call):
        msg = f"apply_expression: expression must be a helper call, got {type(tree.body).__name__!r}"
        raise EvaluationError(msg)

    name, args, kwargs = evaluate_call(tree.body)
    fn = _HELPER_MAP[name]

    # Inject cell_value as first positional argument
    return fn(cell_value, *args, **kwargs)


def apply_transform_spec(transform: dict[str, Any], cell_value: str) -> Any:
    """Apply a transform spec dict (from parser_config TransformSpec) to *cell_value*.

    This is the primary entry point for the engine — it receives already-validated
    Pydantic TransformSpec dicts.

    Args:
        transform: dict with ``helper`` key plus helper-specific keys.
        cell_value: The cell value to transform.

    Returns:
        Transformed value.

    Raises:
        EvaluationError: On unknown helper or argument errors.
    """
    helper = transform.get("helper")
    if helper not in ALLOWED_HELPERS:
        msg = f"apply_transform_spec: {helper!r} is not a whitelisted helper"
        raise EvaluationError(msg)

    fn = _HELPER_MAP[helper]  # type: ignore[index]

    match helper:
        case "parse_date":
            return fn(cell_value, format=transform["format"])
        case "extract_regex":
            return fn(cell_value, pattern=transform["pattern"], group=transform.get("group", 1))
        case "normalize_amount":
            return fn(cell_value, locale=transform.get("locale", "en_US"))
        case "lookup_table":
            return fn(cell_value, map=transform["map"])
        case "coalesce":
            # coalesce takes multiple values; cell_value is already the result of
            # resolving refs — engine handles multi-ref resolution upstream.
            return fn(cell_value)
        case "trim" | "to_upper" | "to_lower":
            return fn(cell_value)
        case "concat":
            # concat also involves multiple refs; engine handles ref resolution upstream.
            return fn(cell_value, sep=transform.get("sep", " "))
        case _:
            msg = f"apply_transform_spec: unhandled helper {helper!r}"
            raise EvaluationError(msg)


def safe_eval_expression(source: str) -> ast.Expression:
    """Alias for safety.verify_expression — for use as a linter (L13 rule).

    Returns the validated AST; callers discard it if they only need
    to confirm the expression is safe.

    Raises:
        DSLSafetyViolation: If unsafe.
        SyntaxError: If unparseable.
    """
    return verify_expression(source)
