"""Tests for the Parser Generator Agent."""

import pytest
from unittest.mock import patch
from pydantic_ai.models.test import TestModel

from open_banca_llm.parser_generator.agent import ParserGeneratorAgent

# A dummy valid parser spec to return from the TestModel
DUMMY_PARSER_SPEC = {
    "version": "0.1.0",
    "bank": "test_bank",
    "defaults": {
        "date_format": "%Y-%m-%d",
        "decimal_separator": ".",
        "thousands_separator": ",",
        "currency": "USD",
        "locale": "en_US"
    },
    "sheets": [
        {
            "name_pattern": ".*",
            "header_row": 1,
            "data_start_row": 2,
            "data_end_marker": "empty_row",
            "account_metadata_extraction": {},
            "column_map": [
                {
                    "target_field": "date",
                    "source": "Date",
                    "required": True,
                    "on_missing": "fail",
                    "transformations": [
                        {"helper": "parse_date", "format": "%Y-%m-%d"}
                    ]
                },
                {
                    "target_field": "amount",
                    "source": "Amount",
                    "required": True,
                    "on_missing": "fail",
                    "transformations": [
                        {"helper": "normalize_amount", "locale": "en_US"}
                    ]
                },
                {
                    "target_field": "description",
                    "source": "Description",
                    "required": True,
                    "on_missing": "fail",
                    "transformations": [
                        {"helper": "trim"}
                    ]
                }
            ],
            "account_type_inference": {
                "savings_columns": [],
                "checking_columns": [],
                "credit_card_columns": [],
                "default": "checking"
            },
            "id_strategy": {
                "strategy": "fingerprint",
                "fingerprint_fields": ["date", "amount", "description"]
            }
        }
    ]
}


@pytest.mark.asyncio
@patch("open_banca_llm.parser_generator.agent.ExcelParser")
async def test_parser_generator_success(mock_parser):
    """Test successful parser generation with dry-run mocked."""
    mock_instance = mock_parser.return_value
    # Mock parse to return some dummy transactions
    mock_instance.parse.return_value = [{"id": "123", "amount": 10.0}]

    # Create a TestModel that returns our dummy spec
    model = TestModel(custom_output_args=DUMMY_PARSER_SPEC)
    
    agent = ParserGeneratorAgent(model=model)
    
    result = await agent.generate(
        bank_id="test_bank",
        sample_csv="Date,Amount,Description\n2023-01-01,10.0,Test",
        target_schema_info="date, amount, description",
        workbook_bytes=b"dummy_bytes",
    )
    
    assert result.success is True
    assert result.parser_spec["bank"] == "test_bank"
    assert len(result.parser_spec["sheets"]) == 1
    
    # Verify the dry-run was called
    mock_instance.parse.assert_called_once()
    assert mock_instance.parse.call_args[0][0] == b"dummy_bytes"


@pytest.mark.asyncio
@patch("open_banca_llm.parser_generator.agent.ExcelParser")
async def test_parser_generator_dry_run_failure(mock_parser):
    """Test parser generation when dry-run fails (e.g. 0 transactions)."""
    mock_instance = mock_parser.return_value
    # Mock parse to return empty list, which should trigger a validation error
    mock_instance.parse.return_value = []

    model = TestModel(custom_output_args=DUMMY_PARSER_SPEC)
    
    agent = ParserGeneratorAgent(model=model)
    
    result = await agent.generate(
        bank_id="test_bank",
        sample_csv="Date,Amount,Description\n2023-01-01,10.0,Test",
        target_schema_info="date, amount, description",
        workbook_bytes=b"dummy_bytes",
    )
    
    # The agent should fail after retries
    assert result.success is False
    assert "Dry-run produced 0 transactions" in result.error_message
