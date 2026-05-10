"""Verify that the open_banca_browser package contains zero LLM imports.

ADR-0001: Mapper-Runner split. The runner must never call any LLM library.
"""
from __future__ import annotations

import ast
import pathlib

_SRC_ROOT = pathlib.Path(__file__).parent.parent / "src" / "open_banca_browser"

_BANNED_MODULES = frozenset(
    [
        "litellm",
        "pydantic_ai",
        "pydantic-ai",
        "anthropic",
        "openai",
        "langchain",
        "llama_index",
        "llama-index",
        "google.generativeai",
        "cohere",
        "mistralai",
    ]
)


def _collect_imports(tree: ast.Module) -> list[str]:
    """Return all top-level module names imported in the AST."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
    return names


def test_no_llm_imports() -> None:
    """No .py file under open_banca_browser/ may import an LLM library."""
    violations: list[str] = []

    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            violations.append(f"{py_file}: SyntaxError — {exc}")
            continue

        for imported in _collect_imports(tree):
            if imported in _BANNED_MODULES:
                violations.append(f"{py_file}: imports banned module {imported!r}")

    assert not violations, (
        "LLM library imports found in scraper runner (violates ADR-0001):\n"
        + "\n".join(violations)
    )
