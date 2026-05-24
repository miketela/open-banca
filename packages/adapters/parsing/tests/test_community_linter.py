"""tests/test_community_linter.py - Tests for MapLinter (L01-L14) and MapVerifier.

All 14 rules are covered. Cosign calls are fully mocked.
No real signing/verification happens here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from open_banca_parsing.community.linter import MapLinter
from open_banca_parsing.community.verifier import MapVerifier, VerifyResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_MAP: dict = {
    "bank_id": "test_bank",
    "version": "1.0.0",
    "schema_version": "1",
    "signature": None,
    "steps": [
        {
            "step_id": "login_navigate",
            "action": "navigate",
            "url": "https://www.testbank.com/login",
        },
        {
            "step_id": "fill_user",
            "action": "fill",
            "selector": "input#username",
            "value_ref": "personal:username",
            "sensitive": False,
        },
        {
            "step_id": "download",
            "action": "download_file",
            "selector": "a[href*='xlsx']",
            "expected_content_type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        },
    ],
}

VALID_PARSER: dict = {
    "version": "1.0.0",
    "bank": "test_bank",
    "defaults": {
        "date_format": "%d/%m/%Y",
        "decimal_separator": ".",
        "thousands_separator": ",",
        "currency": "PAB",
        "locale": "en_US",
    },
    "sheets": [
        {
            "name_pattern": ".*[Ss]aving.*",
            "header_row": 1,
            "data_start_row": 2,
            "data_end_marker": "empty_row",
            "column_map": [
                {
                    "target_field": "date",
                    "source": "Fecha",
                    "required": True,
                    "on_missing": "fail",
                    "transformations": [{"helper": "parse_date", "format": "%d/%m/%Y"}],
                },
                {
                    "target_field": "amount",
                    "source": "Monto",
                    "required": True,
                    "on_missing": "fail",
                    "transformations": [{"helper": "normalize_amount", "locale": "en_US"}],
                },
            ],
        }
    ],
}


def write_bank_dir(
    tmp_path: Path,
    map_data: dict | None = None,
    parser_data: dict | None = None,
) -> Path:
    """Write map.json + parser.json to a temp bank dir and return it."""
    bank_dir = tmp_path / "test_bank"
    bank_dir.mkdir()
    if map_data is not None:
        (bank_dir / "map.json").write_text(json.dumps(map_data), encoding="utf-8")
    if parser_data is not None:
        (bank_dir / "parser.json").write_text(json.dumps(parser_data), encoding="utf-8")
    return bank_dir


# ---------------------------------------------------------------------------
# L01 — BankMap schema
# ---------------------------------------------------------------------------


def test_linter_l01_schema_valid(tmp_path: Path) -> None:
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l01_errors = [e for e in result.errors if e.rule == "L01"]
    assert l01_errors == [], f"Unexpected L01 errors: {l01_errors}"


def test_linter_l01_schema_invalid(tmp_path: Path) -> None:
    """L01 fails when map.json is missing required fields."""
    bad_map = {"bank_id": "x"}  # missing version, steps, schema_version
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    rules = [e.rule for e in result.errors]
    assert "L01" in rules
    assert not result.ok


def test_linter_l01_missing_file(tmp_path: Path) -> None:
    """L01 fails when map.json is absent."""
    bank_dir = write_bank_dir(tmp_path, None, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L01" for e in result.errors)
    assert not result.ok


# ---------------------------------------------------------------------------
# L02 — ParserSpec schema
# ---------------------------------------------------------------------------


def test_linter_l02_schema_valid(tmp_path: Path) -> None:
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l02_errors = [e for e in result.errors if e.rule == "L02"]
    assert l02_errors == []


def test_linter_l02_schema_invalid(tmp_path: Path) -> None:
    """L02 fails when parser.json is missing required sheets field."""
    bad_parser = {"version": "1.0.0", "bank": "x"}  # no sheets
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, bad_parser)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L02" for e in result.errors)
    assert not result.ok


# ---------------------------------------------------------------------------
# L03 — version semver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version",
    ["1.0.0", "0.1.2", "2.3.4-alpha.1", "1.0.0+build.1"],
)
def test_linter_l03_valid_semver(tmp_path: Path, version: str) -> None:
    m = {**VALID_MAP, "version": version}
    p = {**VALID_PARSER, "version": version}
    bank_dir = write_bank_dir(tmp_path, m, p)
    result = MapLinter().lint(bank_dir)
    l03_errors = [e for e in result.errors if e.rule == "L03"]
    assert l03_errors == [], f"Unexpected L03 errors for version {version!r}: {l03_errors}"


@pytest.mark.parametrize("version", ["", "  ", "not-semver", "v1.0", "1.0"])
def test_linter_l03_invalid_version(tmp_path: Path, version: str) -> None:
    m = {**VALID_MAP, "version": version}
    bank_dir = write_bank_dir(tmp_path, m, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L03" for e in result.errors)


# ---------------------------------------------------------------------------
# L04 — no embedded credentials
# ---------------------------------------------------------------------------


def test_linter_l04_embedded_creds_rejected(tmp_path: Path) -> None:
    """L04 fails when a step has sensitive:true with a literal value field."""
    bad_map = {
        **VALID_MAP,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "url": "https://www.testbank.com/login",
            },
            {
                "step_id": "fill_password",
                "action": "fill",
                "selector": "input[type='password']",
                "sensitive": True,
                "value": "hunter2",  # literal cred — L04 violation
            },
        ],
    }
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L04" for e in result.errors)
    assert not result.ok


def test_linter_l04_value_ref_ok(tmp_path: Path) -> None:
    """L04 passes when sensitive:true uses value_ref (not literal value)."""
    good_map = {
        **VALID_MAP,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "url": "https://www.testbank.com/login",
            },
            {
                "step_id": "fill_password",
                "action": "fill",
                "selector": "input[type='password']",
                "sensitive": True,
                "value_ref": "personal:password",
            },
        ],
    }
    bank_dir = write_bank_dir(tmp_path, good_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l04_errors = [e for e in result.errors if e.rule == "L04"]
    assert l04_errors == []


# ---------------------------------------------------------------------------
# L05 — XPath safety
# ---------------------------------------------------------------------------


def test_linter_l05_xpath_safety_rejected(tmp_path: Path) -> None:
    """L05 fails for selectors using //* or descendant::*."""
    bad_map = {
        **VALID_MAP,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "url": "https://www.testbank.com/login",
            },
            {
                "step_id": "bad_wait",
                "action": "wait_for_selector",
                "selector": "//*[@id='form']",  # dangerous XPath
            },
        ],
    }
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L05" for e in result.errors)
    assert not result.ok


def test_linter_l05_descendant_star_rejected(tmp_path: Path) -> None:
    """L05 rejects descendant::* selectors."""
    bad_map = {
        **VALID_MAP,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "url": "https://www.testbank.com/login",
            },
            {
                "step_id": "bad_select",
                "action": "wait_for_selector",
                "selector": "//form/descendant::*",
            },
        ],
    }
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L05" for e in result.errors)


def test_linter_l05_specific_selector_ok(tmp_path: Path) -> None:
    """L05 passes for specific CSS selectors."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l05_errors = [e for e in result.errors if e.rule == "L05"]
    assert l05_errors == []


# ---------------------------------------------------------------------------
# L06 — URL whitelist
# ---------------------------------------------------------------------------


def test_linter_l06_url_whitelist_pass(tmp_path: Path) -> None:
    """L06 passes when all navigate URLs are within the bank's domain."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l06_errors = [e for e in result.errors if e.rule == "L06"]
    assert l06_errors == []


def test_linter_l06_url_whitelist_fail_external(tmp_path: Path) -> None:
    """L06 fails when a navigate URL uses a different domain."""
    bad_map = {
        **VALID_MAP,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "url": "https://www.testbank.com/login",
            },
            {
                "step_id": "exfiltrate",
                "action": "navigate",
                "url": "https://evil.attacker.com/steal",  # external domain
            },
        ],
    }
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L06" for e in result.errors)
    assert not result.ok


def test_linter_l06_http_rejected(tmp_path: Path) -> None:
    """L06 rejects plain http:// navigate URLs."""
    bad_map = {
        **VALID_MAP,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "url": "http://www.testbank.com/login",  # http not https
            },
        ],
    }
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    # First URL is http — L06 should flag it
    assert any(e.rule == "L06" for e in result.errors)


# ---------------------------------------------------------------------------
# L07 — max steps
# ---------------------------------------------------------------------------


def test_linter_l07_max_steps_pass(tmp_path: Path) -> None:
    """L07 passes with a reasonable number of steps."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l07_errors = [e for e in result.errors if e.rule == "L07"]
    assert l07_errors == []


def test_linter_l07_max_steps_fail(tmp_path: Path) -> None:
    """L07 fails when steps >= 200."""
    many_steps = [
        {
            "step_id": f"step_{i}",
            "action": "wait_for_selector",
            "selector": "#element",
        }
        for i in range(200)
    ]
    # Add a navigate step so L06 has a domain to check
    many_steps.insert(
        0,
        {
            "step_id": "login_navigate",
            "action": "navigate",
            "url": "https://www.testbank.com/login",
        },
    )
    big_map = {**VALID_MAP, "steps": many_steps}
    bank_dir = write_bank_dir(tmp_path, big_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L07" for e in result.errors)


# ---------------------------------------------------------------------------
# L08 — max helper invocations per sheet
# ---------------------------------------------------------------------------


def test_linter_l08_max_helpers_pass(tmp_path: Path) -> None:
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l08_errors = [e for e in result.errors if e.rule == "L08"]
    assert l08_errors == []


def test_linter_l08_max_helpers_fail(tmp_path: Path) -> None:
    """L08 fails when a sheet has >= 10 helper invocations total."""
    # Create 5 columns each with 2 helpers = 10 total
    cols = [
        {
            "target_field": f"field_{i}",
            "source": f"Col_{i}",
            "required": False,
            "on_missing": "null",
            "transformations": [
                {"helper": "trim"},
                {"helper": "to_upper"},
            ],
        }
        for i in range(5)
    ]
    heavy_parser = {
        **VALID_PARSER,
        "sheets": [
            {
                "name_pattern": ".*",
                "header_row": 1,
                "data_start_row": 2,
                "data_end_marker": "empty_row",
                "column_map": cols,
            }
        ],
    }
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, heavy_parser)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L08" for e in result.errors)


# ---------------------------------------------------------------------------
# L09 + L13 — re2 compatibility
# ---------------------------------------------------------------------------


def test_linter_l09_re2_compat_pass(tmp_path: Path) -> None:
    """L09 passes for a valid re2 pattern."""
    good_parser = {
        **VALID_PARSER,
        "sheets": [
            {
                "name_pattern": ".*",
                "header_row": 1,
                "data_start_row": 2,
                "data_end_marker": "empty_row",
                "column_map": [
                    {
                        "target_field": "ref",
                        "source": "Ref",
                        "required": False,
                        "on_missing": "null",
                        "transformations": [
                            {"helper": "extract_regex", "pattern": r"\d{4}-\d{4}", "group": 0}
                        ],
                    }
                ],
            }
        ],
    }
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, good_parser)
    result = MapLinter().lint(bank_dir)
    l09_errors = [e for e in result.errors if e.rule in ("L09", "L13")]
    assert l09_errors == []


def test_linter_l09_re2_compat_catastrophic_rejected(tmp_path: Path) -> None:
    """L09+L13: catastrophic backtracking pattern rejected by re2."""
    # re2 rejects lookaheads which could be catastrophic — also certain
    # unsupported constructs. Use a pattern re2 cannot compile.
    bad_parser = {
        **VALID_PARSER,
        "sheets": [
            {
                "name_pattern": ".*",
                "header_row": 1,
                "data_start_row": 2,
                "data_end_marker": "empty_row",
                "column_map": [
                    {
                        "target_field": "ref",
                        "source": "Ref",
                        "required": False,
                        "on_missing": "null",
                        "transformations": [
                            {
                                "helper": "extract_regex",
                                # Lookahead — not supported by re2
                                "pattern": r"(?=.*foo).*bar",
                                "group": 0,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, bad_parser)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule in ("L09", "L13") for e in result.errors)
    assert not result.ok


# ---------------------------------------------------------------------------
# L10 — no eval/exec/__import__
# ---------------------------------------------------------------------------


def test_linter_l10_no_eval_exec_pass(tmp_path: Path) -> None:
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l10_errors = [e for e in result.errors if e.rule == "L10"]
    assert l10_errors == []


def test_linter_l10_eval_rejected(tmp_path: Path) -> None:
    """L10 fails when parser.json contains 'eval' string (in any field, checked on raw JSON).

    We embed 'eval' in the version field (a valid field) so it bypasses L02 Pydantic
    extra="forbid" rejection while still triggering the L10 raw-JSON token scan.
    """
    # Embed 'eval' in the version field — L03 will also flag it but L10 must fire too
    bad_parser = {**VALID_PARSER, "version": "eval-1.0.0"}
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, bad_parser)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L10" for e in result.errors), (
        f"Expected L10 error but got: {[str(e) for e in result.errors]}"
    )


def test_linter_l10_exec_rejected(tmp_path: Path) -> None:
    """L10 fails when parser.json contains 'exec' string (checked on raw JSON)."""
    # Embed 'exec' in the bank field — will fail L02 only if pydantic rejects it,
    # but L10 scans raw JSON before or independently of schema validation
    bad_parser = {**VALID_PARSER, "bank": "exec_bank"}
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, bad_parser)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L10" for e in result.errors), (
        f"Expected L10 error but got: {[str(e) for e in result.errors]}"
    )


# ---------------------------------------------------------------------------
# L11 + L14 — lookup_table size caps
# ---------------------------------------------------------------------------


def test_linter_l11_l14_lookup_table_pass(tmp_path: Path) -> None:
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert not any(e.rule in ("L11", "L14") for e in result.errors)


def test_linter_l11_l14_lookup_table_over_1mb(tmp_path: Path) -> None:
    """L11+L14 fail when a lookup_table exceeds 1 MB serialised."""
    # Create a large lookup table > 1 MB
    big_map_entries = {f"key_{i:07d}": f"val_{i:07d}" for i in range(70_000)}
    heavy_parser = {
        **VALID_PARSER,
        "sheets": [
            {
                "name_pattern": ".*",
                "header_row": 1,
                "data_start_row": 2,
                "data_end_marker": "empty_row",
                "column_map": [
                    {
                        "target_field": "category",
                        "source": "Categoria",
                        "required": False,
                        "on_missing": "null",
                        "transformations": [{"helper": "lookup_table", "map": big_map_entries}],
                    }
                ],
            }
        ],
    }
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, heavy_parser)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule in ("L11", "L14") for e in result.errors)


# ---------------------------------------------------------------------------
# L12 — download_file extension allowlist
# ---------------------------------------------------------------------------


def test_linter_l12_download_extension_pass(tmp_path: Path) -> None:
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    l12_errors = [e for e in result.errors if e.rule == "L12"]
    assert l12_errors == []


def test_linter_l12_download_extension_fail(tmp_path: Path) -> None:
    """L12 fails when download_file has no allowed content-type or extension."""
    bad_map = {
        **VALID_MAP,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "url": "https://www.testbank.com/login",
            },
            {
                "step_id": "bad_download",
                "action": "download_file",
                "selector": "a[href*='zip']",
                "expected_content_type": "application/zip",  # not in allowlist
            },
        ],
    }
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)
    result = MapLinter().lint(bank_dir)
    assert any(e.rule == "L12" for e in result.errors)


# ---------------------------------------------------------------------------
# Integration — banco_general maps must pass linter
# ---------------------------------------------------------------------------


def test_linter_banco_general_passes(tmp_path: Path) -> None:
    """The actual banco_general map.json + parser.json must pass all linter rules."""
    # Locate banco_general relative to this test file
    # test file: packages/adapters/parsing/tests/test_community_linter.py
    # parents[0] = tests/, [1] = parsing/, [2] = adapters/, [3] = packages/, [4] = repo root
    test_file = Path(__file__)
    repo_root = test_file.parents[4]
    banco_dir = repo_root / "packages" / "banks" / "banco_general"

    if not banco_dir.exists():
        pytest.skip(f"banco_general dir not found at {banco_dir}")

    result = MapLinter().lint(banco_dir)
    if not result.ok:
        pytest.fail(f"banco_general failed linter:\n{result}")


# ---------------------------------------------------------------------------
# MapVerifier — runtime enforcement tests
# ---------------------------------------------------------------------------


def test_verifier_unsigned_rejected_when_required(tmp_path: Path) -> None:
    """MapVerifier rejects unsigned maps when OPEN_BANCA_REQUIRE_SIGNED_MAPS=1."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    # No .bundle / .sig / .crt files present

    with patch.dict(os.environ, {"OPEN_BANCA_REQUIRE_SIGNED_MAPS": "1"}):
        verifier = MapVerifier()
        result: VerifyResult = verifier.verify(bank_dir)

    assert result.signed is False
    assert result.ok is False


def test_verifier_lint_fail_rejects_regardless_of_signing(tmp_path: Path) -> None:
    """MapVerifier is not OK if linter fails, even when signing is not required."""
    bad_map = {"bank_id": "x"}  # will fail L01
    bank_dir = write_bank_dir(tmp_path, bad_map, VALID_PARSER)

    with patch.dict(os.environ, {"OPEN_BANCA_REQUIRE_SIGNED_MAPS": "0"}):
        verifier = MapVerifier()
        result = verifier.verify(bank_dir)

    assert not result.ok
    assert len(result.lint_errors) > 0


def test_verifier_signed_accepted(tmp_path: Path) -> None:
    """MapVerifier accepts maps when cosign verify-blob succeeds (mocked)."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)

    # Create stub signature/cert files so the verifier doesn't bail early
    (bank_dir / "map.json.sig").write_text("stub_sig", encoding="utf-8")
    (bank_dir / "map.json.crt").write_text("stub_cert", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Certificate identity: https://github.com/open-banca/open-banca/.github/workflows/sign-bank-maps.yml@refs/tags/bank-maps/v1.0.0\n"
    mock_proc.stderr = "Certificate OIDC issuer: https://token.actions.githubusercontent.com\n"

    with (
        patch.dict(os.environ, {"OPEN_BANCA_REQUIRE_SIGNED_MAPS": "1"}),
        patch("open_banca_parsing.community.verifier.subprocess.run", return_value=mock_proc),
    ):
        verifier = MapVerifier()
        result = verifier.verify(bank_dir)

    assert result.signed is True
    assert result.ok is True
    assert result.cert_subject is not None
    assert "github.com" in (result.cert_subject or "")


def test_verifier_cosign_not_found(tmp_path: Path) -> None:
    """MapVerifier handles missing cosign binary gracefully."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    (bank_dir / "map.json.sig").write_text("stub", encoding="utf-8")
    (bank_dir / "map.json.crt").write_text("stub", encoding="utf-8")

    with (
        patch.dict(os.environ, {"OPEN_BANCA_REQUIRE_SIGNED_MAPS": "1"}),
        patch(
            "open_banca_parsing.community.verifier.subprocess.run",
            side_effect=FileNotFoundError("cosign not found"),
        ),
    ):
        verifier = MapVerifier()
        result = verifier.verify(bank_dir)

    assert result.signed is False
    assert result.ok is False
    assert result.error_detail is not None
    assert "cosign binary not found" in (result.error_detail or "")


def test_verifier_no_require_signed_lint_only(tmp_path: Path) -> None:
    """Without OPEN_BANCA_REQUIRE_SIGNED_MAPS, only lint is enforced."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)

    with patch.dict(os.environ, {"OPEN_BANCA_REQUIRE_SIGNED_MAPS": "0"}):
        verifier = MapVerifier()
        result = verifier.verify(bank_dir)

    assert result.ok is True  # lint passes, signing not required


# ---------------------------------------------------------------------------
# test_cosign_command_format — dry run of sign command structure
# ---------------------------------------------------------------------------


def test_cosign_command_format(tmp_path: Path) -> None:
    """Verify the cosign verify-blob command contains required arguments."""
    bank_dir = write_bank_dir(tmp_path, VALID_MAP, VALID_PARSER)
    (bank_dir / "map.json.sig").write_text("stub", encoding="utf-8")
    (bank_dir / "map.json.crt").write_text("stub", encoding="utf-8")

    captured_cmd: list[list[str]] = []

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = ""
    mock_proc.stderr = ""

    def capture_run(cmd: list[str], **kwargs: object) -> MagicMock:
        captured_cmd.append(list(cmd))
        return mock_proc

    with (
        patch.dict(os.environ, {"OPEN_BANCA_REQUIRE_SIGNED_MAPS": "1"}),
        patch("open_banca_parsing.community.verifier.subprocess.run", side_effect=capture_run),
    ):
        MapVerifier().verify(bank_dir)

    assert len(captured_cmd) == 1, "Expected exactly one subprocess.run call"
    cmd = captured_cmd[0]

    # Verify essential arguments are present
    assert cmd[0] == "cosign"
    assert "verify-blob" in cmd
    assert "--certificate-identity-regexp" in cmd
    assert "--certificate-oidc-issuer" in cmd
    assert "https://token.actions.githubusercontent.com" in cmd
    assert str(bank_dir / "map.json") in cmd
