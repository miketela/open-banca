"""Parser Generator Agent — uses LLM to generate parser.json DSL.

Architecture:
  1. Uses PydanticAI to prompt the LLM (e.g. Claude 3.5 Sonnet or DeepSeek V3).
  2. Receives a sample of the Excel data (CSV format) and the target schema.
  3. Generates a candidate parser.json.
  4. Runs a validation loop (dry-run) against the original Excel file using the Parser Engine.
  5. If the dry-run fails, reinjects the error to the LLM for auto-correction (up to 3 attempts).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model

from open_banca_domain.entities.bank_map import ParserConfig
from open_banca_parsing.engine import ExcelParseError, ExcelParser
from open_banca_parsing.parser_config import ParserSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# I/O types
# ---------------------------------------------------------------------------


class ParserGeneratorResult(BaseModel):
    """Output of the Parser Generator Agent."""

    parser_spec: dict[str, Any] = Field(description="The generated parser.json as a dict")
    attempts: int = Field(description="Number of attempts it took to generate a valid parser")
    success: bool = Field(description="Whether the generation and dry-run were successful")
    error_message: str | None = Field(default=None, description="Error message if failed")


@dataclass
class ParserGeneratorDeps:
    """Dependencies for the PydanticAI Agent."""

    bank_id: str
    sample_csv: str
    target_schema_info: str
    workbook_bytes: bytes


# ---------------------------------------------------------------------------
# Agent Definition
# ---------------------------------------------------------------------------

# The agent returns a ParserSpec model (which is our DSL schema).
# We use PydanticAI's structured output capability.
parser_agent = Agent(
    deps_type=ParserGeneratorDeps,
    output_type=ParserSpec,
    system_prompt=(
        "You are an expert data engineer and Excel parsing specialist. "
        "Your task is to generate a deterministic parser.json configuration for a bank's Excel statement. "
        "You will receive a sample of the Excel data (in CSV format) and the target schema description. "
        "You must output a valid JSON configuration matching the ParserSpec schema. "
        "Use ONLY the whitelisted helpers (parse_date, extract_regex, normalize_amount, lookup_table, coalesce, trim, to_upper, to_lower, concat). "
        "Ensure the 'name_pattern' regex matches the sheet name. "
        "Ensure 'header_row' and 'data_start_row' are correct (1-based indices). "
        "Set 'data_end_marker' to 'empty_row' or 'last_row' appropriately. "
        "Map the columns to the target schema fields."
    ),
    output_retries=3,
)


@parser_agent.output_validator
def validate_parser_spec(ctx: RunContext[ParserGeneratorDeps], result: ParserSpec) -> ParserSpec:
    """Validate the generated ParserSpec by running a dry-run against the actual Excel file."""
    # 1. Static validation is already done by PydanticAI parsing the result into ParserSpec.

    # 2. Dry-Run (Ejecución de Prueba)
    engine = ExcelParser()

    # Convert ParserSpec to ParserConfig (domain entity)
    parser_config = ParserConfig(**result.model_dump(mode="json", exclude_none=True))

    try:
        transactions = engine.parse(ctx.deps.workbook_bytes, parser_config)
    except ExcelParseError as exc:
        raise ValueError(f"Dry-run failed with parse error: {exc}") from exc
    except Exception as exc:
        logger.error("Dry-run failed: %s", exc)
        raise ValueError(f"Dry-run failed with unexpected error: {exc}") from exc

    # 3. Data Quality Check
    if not transactions:
        raise ValueError("Dry-run produced 0 transactions. Check data_start_row, sheet name_pattern, and column mappings.")

    # We could add more checks here (e.g. dropped rows percentage), but for now,
    # if it parses without throwing ExcelParseError and returns transactions, it's considered valid.

    return result


# ---------------------------------------------------------------------------
# ParserGeneratorAgent wrapper
# ---------------------------------------------------------------------------


class ParserGeneratorAgent:
    """Wrapper class for the Parser Generator logic."""

    def __init__(self, model: Model | str | None = None) -> None:
        self._model = model or "anthropic:claude-3-5-sonnet-latest"

    async def generate(
        self,
        bank_id: str,
        sample_csv: str,
        target_schema_info: str,
        workbook_bytes: bytes,
    ) -> ParserGeneratorResult:
        """Generate a parser.json configuration.

        Args:
            bank_id: The bank identifier.
            sample_csv: A string containing a CSV representation of the Excel sample.
            target_schema_info: Description of the target schema fields.
            workbook_bytes: The raw bytes of the original Excel file (for dry-run).

        Returns:
            ParserGeneratorResult containing the generated parser dict.
        """
        deps = ParserGeneratorDeps(
            bank_id=bank_id,
            sample_csv=sample_csv,
            target_schema_info=target_schema_info,
            workbook_bytes=workbook_bytes,
        )

        prompt = (
            f"Bank ID: {bank_id}\n\n"
            f"Target Schema Info:\n{target_schema_info}\n\n"
            f"Sample Data (CSV):\n{sample_csv}\n\n"
            "Please generate the parser.json configuration."
        )

        try:
            # Run the agent. The result_validator will automatically handle the dry-run
            # and trigger retries if it raises a ValueError.
            result = await parser_agent.run(
                prompt,
                deps=deps,
                model=self._model,
            )

            return ParserGeneratorResult(
                parser_spec=result.output.model_dump(mode="json", exclude_none=True),
                attempts=1, # PydanticAI doesn't easily expose retry count in the result yet
                success=True,
            )
        except Exception as exc:
            import traceback
            logger.error("Parser generation failed after retries: %s\n%s", exc, traceback.format_exc())
            return ParserGeneratorResult(
                parser_spec={},
                attempts=3,
                success=False,
                error_message=str(exc),
            )
