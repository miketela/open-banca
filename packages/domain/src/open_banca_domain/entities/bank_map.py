"""BankMap entity — versioned scrape map for a bank."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StepSpec(BaseModel):
    """A single step in a bank scrape map."""

    model_config = ConfigDict(frozen=True, extra="allow")

    step_id: str
    action: str
    target: str | None = None


class ParserConfig(BaseModel):
    """Excel parser DSL configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    sheet_index: int = 0
    header_row: int = 0


class BankMap(BaseModel):
    """Versioned map describing how to scrape a bank."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bank_id: str
    version: str
    steps: list[StepSpec]
    schema_version: str
    signature: str | None = None
