"""ExcelParserPort — declarative DSL-driven Excel parser."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from open_banca_domain.entities.bank_map import ParserConfig
from open_banca_domain.entities.transaction import Transaction


@runtime_checkable
class ExcelParserPort(Protocol):
    """Executes a parser_spec DSL against raw Excel bytes."""

    def parse(self, workbook_bytes: bytes, parser_spec: ParserConfig) -> list[Transaction]: ...
