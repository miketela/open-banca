"""parser_config.py — Pydantic schema for parser.json DSL configuration.

Adapter-local rich schema. The domain ParserConfig (extra="allow") accepts the full
JSON; the engine re-validates via this schema before execution.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OnMissing(StrEnum):
    FAIL = "fail"
    NULL = "null"
    DEFAULT = "default"


class DataEndMarker(StrEnum):
    EMPTY_ROW = "empty_row"
    LAST_ROW = "last_row"


# ---------------------------------------------------------------------------
# Transformation helpers (one entry per helper invocation in a pipeline)
# ---------------------------------------------------------------------------


class ParseDateTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["parse_date"]
    format: str = Field(..., description="strptime-compatible format, e.g. %d/%m/%Y")


class ExtractRegexTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["extract_regex"]
    pattern: str
    group: int = Field(default=1, ge=0)


class NormalizeAmountTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["normalize_amount"]
    locale: str = Field(default="en_US", description="e.g. pa_PA, en_US")


class LookupTableTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["lookup_table"]
    map: dict[str, str] = Field(..., max_length=100_000)

    @field_validator("map")
    @classmethod
    def cap_entries(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 100_000:
            msg = "lookup_table map exceeds 100 000-entry cap (CWE-400)"
            raise ValueError(msg)
        return v


class CoalesceTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["coalesce"]
    refs: list[str] = Field(..., min_length=1, max_length=50)


class TrimTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["trim"]


class ToUpperTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["to_upper"]


class ToLowerTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["to_lower"]


class ConcatTransform(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    helper: Literal["concat"]
    sep: str = Field(default=" ", max_length=16)
    refs: list[str] = Field(..., min_length=1, max_length=50)


# Union of all allowed transform types (closed set — no arbitrary helpers)
TransformSpec = (
    ParseDateTransform
    | ExtractRegexTransform
    | NormalizeAmountTransform
    | LookupTableTransform
    | CoalesceTransform
    | TrimTransform
    | ToUpperTransform
    | ToLowerTransform
    | ConcatTransform
)


# ---------------------------------------------------------------------------
# column_map entry
# ---------------------------------------------------------------------------


class ColumnMapEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_field: str = Field(..., description="Canonical field name, e.g. date, amount, description")
    source: str = Field(..., description="Column header name or letter (A, B, C…)")
    transformations: list[TransformSpec] = Field(default_factory=list)
    required: bool = True
    on_missing: OnMissing = OnMissing.FAIL
    default_value: str | None = None


# ---------------------------------------------------------------------------
# account_type_inference
# ---------------------------------------------------------------------------


class AccountTypeInference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    savings_columns: list[str] = Field(default_factory=list)
    checking_columns: list[str] = Field(default_factory=list)
    credit_card_columns: list[str] = Field(default_factory=list)
    default: str = "checking"


# ---------------------------------------------------------------------------
# id_strategy
# ---------------------------------------------------------------------------


class IdStrategy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["embedded", "fingerprint"] = "fingerprint"
    embedded_field: str | None = None
    fingerprint_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sheet spec
# ---------------------------------------------------------------------------


class SheetSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name_pattern: str = Field(..., description="Regex matching the sheet name")
    header_row: int = Field(..., ge=1, description="1-based row index of column headers")
    data_start_row: int = Field(..., ge=1, description="1-based row index of first data row")
    data_end_marker: DataEndMarker = DataEndMarker.EMPTY_ROW
    data_end_regex: str | None = None
    account_metadata_extraction: dict[str, str] = Field(
        default_factory=dict,
        description="Maps cell address (e.g. B3) to canonical metadata field name",
    )
    column_map: list[ColumnMapEntry] = Field(..., min_length=1)
    account_type_inference: AccountTypeInference = Field(
        default_factory=AccountTypeInference
    )
    id_strategy: IdStrategy = Field(default_factory=IdStrategy)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class ParserDefaults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date_format: str = "%d/%m/%Y"
    decimal_separator: str = "."
    thousands_separator: str = ","
    currency: str = "PAB"
    locale: str = "en_US"


# ---------------------------------------------------------------------------
# Top-level parser spec
# ---------------------------------------------------------------------------


class ParserSpec(BaseModel):
    """Full adapter-local schema for a parser.json file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    bank: str
    defaults: ParserDefaults = Field(default_factory=ParserDefaults)
    sheets: list[SheetSpec] = Field(..., min_length=1)

    # Legacy flat fields from domain ParserConfig stub — accepted for compat
    sheet_index: int | None = None
    header_row: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParserSpec:
        """Validate raw dict (e.g. model_dump of domain ParserConfig) into rich ParserSpec."""
        return cls.model_validate(data)
