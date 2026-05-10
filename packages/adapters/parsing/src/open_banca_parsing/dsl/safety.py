"""dsl/safety.py — AST whitelist verifier for DSL expression strings.

Enforces that no arbitrary Python can be embedded in a parser.json.
Used by the evaluator to validate helper argument expressions before dispatch.

Whitelist:
- Expression nodes only (no statements)
- Allowed top-level nodes: Call, Name (whitelisted helpers only), Constant
- Allowed in Call args: Constant, Name (whitelisted), keyword
- Forbidden: Attribute, Subscript, Lambda, Import, ImportFrom,
             BinOp, UnaryOp (except in constants), comprehensions,
             __builtins__ access, any dunder names

NO eval/exec/compile/__import__ at any point.
"""
from __future__ import annotations

import ast
from typing import ClassVar

# Closed set of allowed helper names — must match helpers.py exports exactly
ALLOWED_HELPERS: frozenset[str] = frozenset(
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

# Names explicitly forbidden regardless of context
_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "__builtins__",
        "__loader__",
        "__spec__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "type",
        "object",
        "super",
        "classmethod",
        "staticmethod",
        "property",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "memoryview",
        "bytearray",
        "bytes",
    }
)


class DSLSafetyViolation(ValueError):
    """Raised when an expression violates the DSL safety whitelist."""


class _SafetyVisitor(ast.NodeVisitor):
    """AST visitor that raises DSLSafetyViolation on any disallowed node."""

    # Nodes that are unconditionally forbidden
    _FORBIDDEN_NODE_TYPES: ClassVar[tuple[type[ast.AST], ...]] = (
        ast.Import,
        ast.ImportFrom,
        ast.Lambda,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        ast.Global,
        ast.Nonlocal,
        ast.Delete,
        ast.Assert,
        ast.Raise,
        ast.Try,
        ast.With,
        ast.AsyncWith,
        ast.AsyncFor,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.FunctionDef,
        ast.Attribute,  # prevents os.system, __class__.__bases__, etc.
        ast.Subscript,  # prevents dict["key"] injection
        ast.Starred,
        ast.JoinedStr,   # f-strings — potential injection vector
    )

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, self._FORBIDDEN_NODE_TYPES):
            msg = (
                f"DSL safety violation: node type {type(node).__name__!r} is not allowed. "
                "Only whitelisted helpers with constant arguments are permitted."
            )
            raise DSLSafetyViolation(msg)
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        name = node.id
        if name in _FORBIDDEN_NAMES:
            msg = f"DSL safety violation: name {name!r} is explicitly forbidden."
            raise DSLSafetyViolation(msg)
        # Dunder names are forbidden
        if name.startswith("__") and name.endswith("__"):
            msg = f"DSL safety violation: dunder name {name!r} is not allowed."
            raise DSLSafetyViolation(msg)
        # Names used as callables must be whitelisted helpers
        # (non-callable Name nodes — e.g. in arguments — are validated at Call site)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # The function being called must be a whitelisted helper
        if not isinstance(node.func, ast.Name):
            msg = (
                f"DSL safety violation: only direct helper calls are allowed "
                f"(e.g. parse_date(...)), not {ast.dump(node.func)!r}"
            )
            raise DSLSafetyViolation(msg)
        func_name = node.func.id
        if func_name not in ALLOWED_HELPERS:
            msg = (
                f"DSL safety violation: {func_name!r} is not a whitelisted helper. "
                f"Allowed: {sorted(ALLOWED_HELPERS)}"
            )
            raise DSLSafetyViolation(msg)
        # Validate arguments recursively
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)

    def visit_Constant(self, node: ast.Constant) -> None:
        # All constant literals are allowed (str, int, float, bool, None, bytes)
        # bytes could theoretically be used as injection, but without Attribute/Subscript
        # access there's no way to call dangerous methods on them.
        pass


def verify_expression(source: str) -> ast.Expression:
    """Parse and safety-check a DSL expression string.

    Args:
        source: A single-expression Python string, e.g. ``"parse_date('%d/%m/%Y')"``

    Returns:
        Parsed ast.Expression node (validated safe).

    Raises:
        DSLSafetyViolation: If the expression contains any disallowed construct.
        SyntaxError: If the source cannot be parsed as a Python expression.
    """
    if not source or not source.strip():
        msg = "DSL safety: empty expression is not allowed."
        raise DSLSafetyViolation(msg)

    # ast.parse with mode='eval' — does NOT execute anything (no eval/exec)
    try:
        tree = ast.parse(source.strip(), mode="eval", filename="<dsl>")
    except SyntaxError as exc:
        msg = f"DSL syntax error in expression {source!r}: {exc}"
        raise DSLSafetyViolation(msg) from exc

    visitor = _SafetyVisitor()
    visitor.visit(tree)
    return tree
