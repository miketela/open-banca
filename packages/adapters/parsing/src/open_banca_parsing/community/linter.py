"""community/linter.py — MapLinter: 15-rule static validator for community bank maps.

Rules implemented
-----------------
L01  BankMap Pydantic validation (map.json schema).
L02  ParserSpec Pydantic validation (parser.json schema).
L03  version field non-empty and semver-like (major.minor.patch[-suffix]).
L04  No sensitive:true step that also carries a literal `value` field (embedded cred).
L05  No XPath expression using //* or descendant::* (O(n²) DoS selector).
L06  navigate URLs must share the eTLD+1 domain of the bank's first navigate step.
L07  Total step count < 200 (sanity cap).
L08  DSL helper invocations per SheetSpec < 10.
L09  Every extract_regex pattern compiles with re2 (ReDoS immunity, CWE-1333).
L10  No eval / exec / __import__ token in any DSL expression string field.
L11  All lookup_table maps: combined JSON-serialised size ≤ 1 MB (CWE-400).
L12  download_file steps: expected_content_type must reference allowed extensions
     (.xlsx, .xls, .csv) or selector must only match those extensions.
L13  Re2 compile check (T15 addition — explicit per ADR-0007-amendment).
L14  lookup_table per-map size ≤ 1 MB serialised (T15 addition — per-map cap).
L15  prompt_user steps: question_selector non-null, field_key valid regex
     (^[a-z][a-z0-9_]{2,32}$), selector non-null, and question_selector unique
     within the map (ADR-0021).

CLI usage::

    python -m open_banca_parsing.community.linter <bank_dir>

Returns exit code 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import re2  # google-re2 — O(n) linear time

from open_banca_domain.entities.bank_map import BankMap
from open_banca_parsing.parser_config import ExtractRegexTransform, LookupTableTransform, ParserSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semver-like pattern (permissive: major.minor.patch[-prerelease][+build])
# ---------------------------------------------------------------------------
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9._-]+)?(?:\+[a-zA-Z0-9._-]+)?$")

# ---------------------------------------------------------------------------
# Forbidden DSL token patterns (defense-in-depth; L10)
# ---------------------------------------------------------------------------
_FORBIDDEN_DSL_TOKENS: tuple[str, ...] = ("eval", "exec", "__import__")

# ---------------------------------------------------------------------------
# Allowed download extensions (L12)
# ---------------------------------------------------------------------------
_ALLOWED_DOWNLOAD_EXTENSIONS: frozenset[str] = frozenset({".xlsx", ".xls", ".csv"})

# Content-type → allowed extensions mapping
_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/csv",
        "application/csv",
        "application/octet-stream",  # generic fallback
    }
)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LintError:
    rule: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.message}"


@dataclass
class LintResult:
    bank_dir: Path
    errors: list[LintError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def add(self, rule: str, message: str) -> None:
        self.errors.append(LintError(rule=rule, message=message))

    def __str__(self) -> str:
        if self.ok:
            return f"PASS {self.bank_dir}"
        lines = [f"FAIL {self.bank_dir} — {len(self.errors)} error(s):"]
        lines.extend(f"  {e}" for e in self.errors)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MapLinter
# ---------------------------------------------------------------------------


_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,32}$")


class MapLinter:
    """Static security linter for community bank maps.

    Runs 15 rules over a bank directory containing map.json and parser.json.
    Never loads arbitrary Python; all validation is Pydantic + static analysis.
    """

    def lint(self, bank_dir: Path) -> LintResult:
        """Run all 15 rules against *bank_dir*.

        Args:
            bank_dir: Path to a bank directory containing map.json and parser.json.

        Returns:
            LintResult with ok=True if all rules pass, errors otherwise.
        """
        result = LintResult(bank_dir=bank_dir)

        map_path = bank_dir / "map.json"
        parser_path = bank_dir / "parser.json"

        # Load raw JSON — abort early on parse failure
        raw_map = self._load_json(map_path, result, "L01")
        raw_parser = self._load_json(parser_path, result, "L02")

        # Validate schemas (L01, L02)
        bank_map = self._validate_bank_map(raw_map, result)
        parser_spec = self._validate_parser_spec(raw_parser, result)

        # Rule L03 — versioning
        if bank_map is not None:
            self._check_version(bank_map.version, result, "map.json")
        if parser_spec is not None:
            self._check_version(parser_spec.version, result, "parser.json")

        # Rules over map.json steps
        if bank_map is not None:
            self._check_l04_no_embedded_creds(bank_map, result)
            self._check_l05_xpath_safety(bank_map, result)
            self._check_l06_url_whitelist(bank_map, result)
            self._check_l07_max_steps(bank_map, result)
            self._check_l12_download_extensions(bank_map, result)
            self._check_l15_prompt_user(bank_map, result)

        # Rules over parser.json
        if parser_spec is not None:
            self._check_l08_max_helper_invocations(parser_spec, result)
            self._check_l09_l13_re2_compat(parser_spec, result)
            self._check_l10_no_eval_exec(raw_parser, result)
            self._check_l11_l14_lookup_table_size(parser_spec, result)

        return result

    # ------------------------------------------------------------------
    # JSON loading helpers
    # ------------------------------------------------------------------

    def _load_json(
        self,
        path: Path,
        result: LintResult,
        rule: str,
    ) -> dict[str, Any] | None:
        if not path.exists():
            result.add(rule, f"File not found: {path}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result.add(rule, f"Invalid JSON in {path.name}: {exc}")
            return None

    # ------------------------------------------------------------------
    # L01 — BankMap schema validation
    # ------------------------------------------------------------------

    def _validate_bank_map(self, raw: dict[str, Any] | None, result: LintResult) -> BankMap | None:
        if raw is None:
            return None
        try:
            return BankMap.model_validate(raw)
        except Exception as exc:
            result.add("L01", f"map.json schema invalid: {exc}")
            return None

    # ------------------------------------------------------------------
    # L02 — ParserSpec schema validation
    # ------------------------------------------------------------------

    def _validate_parser_spec(
        self, raw: dict[str, Any] | None, result: LintResult
    ) -> ParserSpec | None:
        if raw is None:
            return None
        try:
            return ParserSpec.model_validate(raw)
        except Exception as exc:
            result.add("L02", f"parser.json schema invalid: {exc}")
            return None

    # ------------------------------------------------------------------
    # L03 — version non-empty and semver-like
    # ------------------------------------------------------------------

    def _check_version(self, version: str, result: LintResult, filename: str) -> None:
        if not version or not version.strip():
            result.add("L03", f"{filename}: version field is empty")
            return
        if not _SEMVER_RE.match(version.strip()):
            result.add(
                "L03",
                f"{filename}: version {version!r} is not semver-like "
                "(expected major.minor.patch[-prerelease][+build])",
            )

    # ------------------------------------------------------------------
    # L04 — no sensitive:true step with a literal `value` field
    # ------------------------------------------------------------------

    def _check_l04_no_embedded_creds(self, bank_map: BankMap, result: LintResult) -> None:
        for step in bank_map.steps:
            # BankMap steps use extra="allow" so raw fields accessible via model_extra
            extra = step.model_extra or {}
            sensitive = extra.get("sensitive", False)
            has_literal_value = "value" in extra
            if sensitive and has_literal_value:
                result.add(
                    "L04",
                    f"Step {step.step_id!r}: sensitive:true with embedded literal `value` "
                    "field — credentials must not be hardcoded in map.json",
                )

    # ------------------------------------------------------------------
    # L05 — XPath selector safety: reject //* and descendant::*
    # ------------------------------------------------------------------

    def _check_l05_xpath_safety(self, bank_map: BankMap, result: LintResult) -> None:
        _DANGEROUS_XPATH = ("//*", "descendant::*")
        for step in bank_map.steps:
            extra = step.model_extra or {}
            for field_name in ("selector", "from_selector", "to_selector"):
                selector = extra.get(field_name, "")
                if not isinstance(selector, str):
                    continue
                for dangerous in _DANGEROUS_XPATH:
                    if dangerous in selector:
                        result.add(
                            "L05",
                            f"Step {step.step_id!r} field {field_name!r}: "
                            f"XPath {dangerous!r} is a DoS risk (O(n²) traversal) — "
                            "use specific selectors instead",
                        )

    # ------------------------------------------------------------------
    # L06 — navigate URLs must be within the bank's declared domain
    # ------------------------------------------------------------------

    def _check_l06_url_whitelist(self, bank_map: BankMap, result: LintResult) -> None:
        # Derive the bank's domain from the first navigate step
        bank_domain = self._derive_bank_domain(bank_map)
        if bank_domain is None:
            # No navigate step found — nothing to check
            return

        for step in bank_map.steps:
            if step.action != "navigate":
                continue
            extra = step.model_extra or {}
            url = extra.get("url", "")
            if not isinstance(url, str) or not url:
                continue
            try:
                parsed = urlparse(url)
            except ValueError:
                result.add("L06", f"Step {step.step_id!r}: unparseable URL {url!r}")
                continue

            # Require https
            if parsed.scheme != "https":
                result.add(
                    "L06",
                    f"Step {step.step_id!r}: URL {url!r} uses non-https scheme "
                    f"{parsed.scheme!r} — only https is allowed",
                )
                continue

            step_domain = self._etld1(parsed.netloc)
            if step_domain != bank_domain:
                result.add(
                    "L06",
                    f"Step {step.step_id!r}: navigate URL {url!r} domain {step_domain!r} "
                    f"is outside the bank's declared domain {bank_domain!r} "
                    "(T05: exfiltration prevention)",
                )

    def _derive_bank_domain(self, bank_map: BankMap) -> str | None:
        """Return eTLD+1 of the first navigate step's URL, or None if absent."""
        for step in bank_map.steps:
            if step.action == "navigate":
                extra = step.model_extra or {}
                url = extra.get("url", "")
                if isinstance(url, str) and url:
                    try:
                        parsed = urlparse(url)
                        return self._etld1(parsed.netloc)
                    except ValueError:
                        return None
        return None

    @staticmethod
    def _etld1(netloc: str) -> str:
        """Extract eTLD+1 from a netloc string (simplified — last two dot-parts).

        Sufficient for single-bank enforcement; no full PSL needed here.
        """
        # Strip port if present
        host = netloc.split(":")[0].lower()
        parts = host.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host

    # ------------------------------------------------------------------
    # L07 — max steps < 200
    # ------------------------------------------------------------------

    def _check_l07_max_steps(self, bank_map: BankMap, result: LintResult) -> None:
        count = len(bank_map.steps)
        if count >= 200:
            result.add(
                "L07",
                f"map.json has {count} steps; maximum allowed is 199 "
                "(L07 sanity cap — large maps may hide payloads)",
            )

    # ------------------------------------------------------------------
    # L08 — max DSL helper invocations per SheetSpec < 10
    # ------------------------------------------------------------------

    def _check_l08_max_helper_invocations(
        self, parser_spec: ParserSpec, result: LintResult
    ) -> None:
        for sheet in parser_spec.sheets:
            total_helpers = sum(len(col.transformations) for col in sheet.column_map)
            if total_helpers >= 10:
                result.add(
                    "L08",
                    f"Sheet pattern {sheet.name_pattern!r}: "
                    f"{total_helpers} helper invocations across all columns; "
                    "maximum allowed per sheet is 9 (L08 cap)",
                )

    # ------------------------------------------------------------------
    # L09 + L13 — re2 compatibility for every extract_regex pattern
    #
    # L09 = every extract_regex pattern compiles with re2 (broad check)
    # L13 = explicit re2 compile check as required by ADR-0007-amendment T15
    # Both are implemented here as a single pass; errors reference both IDs
    # when the compile fails (they test the same property for defence-in-depth).
    # ------------------------------------------------------------------

    def _check_l09_l13_re2_compat(self, parser_spec: ParserSpec, result: LintResult) -> None:
        for sheet in parser_spec.sheets:
            for col in sheet.column_map:
                for transform in col.transformations:
                    if not isinstance(transform, ExtractRegexTransform):
                        continue
                    pattern = transform.pattern
                    try:
                        re2.compile(pattern)
                    except Exception as exc:
                        result.add(
                            "L09",
                            f"Sheet {sheet.name_pattern!r}, column {col.target_field!r}: "
                            f"extract_regex pattern {pattern!r} is not RE2-compatible: {exc} "
                            "(CWE-1333 — ReDoS risk; use linear-time patterns)",
                        )
                        result.add(
                            "L13",
                            f"Sheet {sheet.name_pattern!r}, column {col.target_field!r}: "
                            f"re2 compile check failed for pattern {pattern!r}: {exc} "
                            "(ADR-0007-amendment T15)",
                        )

    # ------------------------------------------------------------------
    # L10 — no eval/exec/__import__ tokens in any string in the raw parser JSON
    # ------------------------------------------------------------------

    def _check_l10_no_eval_exec(
        self, raw_parser: dict[str, Any] | None, result: LintResult
    ) -> None:
        if raw_parser is None:
            return
        serialised = json.dumps(raw_parser)
        for token in _FORBIDDEN_DSL_TOKENS:
            if token in serialised:
                result.add(
                    "L10",
                    f"parser.json contains forbidden token {token!r} — "
                    "no eval/exec/__import__ in DSL expressions (defense-in-depth)",
                )

    # ------------------------------------------------------------------
    # L11 + L14 — lookup_table size caps
    #
    # L11 = combined total of all lookup_table maps ≤ 1 MB JSON-serialised
    # L14 = per-map individual cap ≤ 1 MB (T15 addition, ADR-0007-amendment)
    # ------------------------------------------------------------------

    def _check_l11_l14_lookup_table_size(self, parser_spec: ParserSpec, result: LintResult) -> None:
        _MAX_BYTES = 1_000_000  # 1 MB
        total_bytes = 0
        for sheet in parser_spec.sheets:
            for col in sheet.column_map:
                for transform in col.transformations:
                    if not isinstance(transform, LookupTableTransform):
                        continue
                    serialised = json.dumps(transform.map, ensure_ascii=False)
                    size = len(serialised.encode("utf-8"))
                    # L14 — per-map individual cap
                    if size > _MAX_BYTES:
                        result.add(
                            "L14",
                            f"Sheet {sheet.name_pattern!r}, column {col.target_field!r}: "
                            f"single lookup_table map is {size:,} bytes; "
                            f"limit is {_MAX_BYTES:,} bytes (ADR-0007-amendment T15)",
                        )
                    total_bytes += size
        # L11 — combined total cap
        if total_bytes > _MAX_BYTES:
            result.add(
                "L11",
                f"parser.json: total lookup_table size is {total_bytes:,} bytes; "
                f"limit is {_MAX_BYTES:,} bytes (CWE-400)",
            )

    # ------------------------------------------------------------------
    # L12 — download_file extension / content-type allowlist
    # ------------------------------------------------------------------

    def _check_l12_download_extensions(self, bank_map: BankMap, result: LintResult) -> None:
        for step in bank_map.steps:
            if step.action != "download_file":
                continue
            extra = step.model_extra or {}
            ct = extra.get("expected_content_type", "")
            selector = extra.get("selector", "")

            ct_ok = isinstance(ct, str) and ct.lower() in _ALLOWED_CONTENT_TYPES

            # Check selector references allowed extensions
            selector_ok = False
            if isinstance(selector, str):
                for ext in _ALLOWED_DOWNLOAD_EXTENSIONS:
                    if ext.lstrip(".") in selector.lower():
                        selector_ok = True
                        break

            if not ct_ok and not selector_ok:
                result.add(
                    "L12",
                    f"Step {step.step_id!r}: download_file has no allowed "
                    f"content_type ({_ALLOWED_CONTENT_TYPES}) "
                    f"and selector {selector!r} does not reference .xlsx/.xls/.csv "
                    "(L12 extension allowlist — prevent unexpected file formats)",
                )

    # ------------------------------------------------------------------
    # L15 — prompt_user step validation (ADR-0021)
    # ------------------------------------------------------------------

    def _check_l15_prompt_user(self, bank_map: BankMap, result: LintResult) -> None:
        """L15: validate all prompt_user steps.

        Checks:
          1. ``question_selector`` is present and non-empty.
          2. ``field_key`` matches regex ``^[a-z][a-z0-9_]{2,32}$``.
          3. ``selector`` (answer input) is present and non-empty.
          4. ``question_selector`` values are unique within the map (prevent cross-contamination).
        """
        seen_question_selectors: dict[str, str] = {}  # question_selector → first step_id

        for step in bank_map.steps:
            if step.action != "prompt_user":
                continue

            extra = step.model_extra or {}
            question_selector: str = extra.get("question_selector", "") or ""
            field_key: str = extra.get("field_key", "") or ""
            answer_selector: str = extra.get("selector", step.target or "") or ""

            if not question_selector:
                result.add(
                    "L15",
                    f"Step {step.step_id!r}: prompt_user requires non-empty "
                    "``question_selector`` field (ADR-0021)",
                )

            if not field_key:
                result.add(
                    "L15",
                    f"Step {step.step_id!r}: prompt_user requires non-empty "
                    "``field_key`` field (ADR-0021)",
                )
            elif not _FIELD_KEY_RE.match(field_key):
                result.add(
                    "L15",
                    f"Step {step.step_id!r}: prompt_user field_key {field_key!r} "
                    r"must match ^[a-z][a-z0-9_]{2,32}$ "
                    "(ADR-0021 — lowercase letters, digits, underscores, 3-33 chars)",
                )

            if not answer_selector:
                result.add(
                    "L15",
                    f"Step {step.step_id!r}: prompt_user requires non-empty "
                    "``selector`` field for the answer input (ADR-0021)",
                )

            # Uniqueness check for question_selector within this map
            if question_selector:
                if question_selector in seen_question_selectors:
                    result.add(
                        "L15",
                        f"Step {step.step_id!r}: prompt_user question_selector "
                        f"{question_selector!r} duplicates step "
                        f"{seen_question_selectors[question_selector]!r} — "
                        "each question_selector must be unique within a map to prevent "
                        "cache key collisions (ADR-0021)",
                    )
                else:
                    seen_question_selectors[question_selector] = step.step_id


# ---------------------------------------------------------------------------
# CLI entry point  (python -m open_banca_parsing.community.linter <bank_dir>)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m open_banca_parsing.community.linter",
        description="Run the 15-rule community map linter against a bank directory.",
    )
    p.add_argument(
        "bank_dir",
        type=Path,
        help="Path to the bank directory containing map.json and parser.json",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Emit DEBUG log output",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on pass, 1 on failure."""
    args = _build_parser().parse_args(argv)
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(message)s")

    linter = MapLinter()
    result = linter.lint(args.bank_dir)
    print(result)

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
