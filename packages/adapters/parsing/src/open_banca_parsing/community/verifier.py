"""community/verifier.py — MapVerifier: runtime enforcement of linting + cosign signing.

Verification pipeline:
1. Run MapLinter against bank_dir (all 14 rules).
2. If OPEN_BANCA_REQUIRE_SIGNED_MAPS=1, also verify cosign signature via
   `cosign verify-blob` using GitHub OIDC keyless signing.
3. All attempts are logged to the audit logger for traceability.

By default (env var absent / falsy) the cosign check is informational only.
The linter check is ALWAYS run.

Usage::

    from open_banca_parsing.community.verifier import MapVerifier

    verifier = MapVerifier()
    result = verifier.verify(bank_dir)
    if not result.ok:
        raise RuntimeError(result.errors)
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from open_banca_parsing.community.linter import LintResult, MapLinter

logger = logging.getLogger(__name__)
_AUDIT_LOG = logging.getLogger("open_banca.audit")

# GitHub OIDC issuer for keyless cosign verification
_GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
# Regexp matching any GitHub Actions workflow from the open-banca org (or forks)
_CERT_IDENTITY_REGEXP = "https://github.com/.+/.github/workflows/.+"


@dataclass(frozen=True)
class VerifyResult:
    """Result of a full map verification (lint + optional cosign)."""

    bank_dir: Path
    ok: bool
    lint_errors: list[str]
    signed: bool
    cert_subject: str | None
    cert_issuer: str | None
    error_detail: str | None = None


@dataclass
class _MutableVerifyResult:
    """Mutable builder — converted to VerifyResult on return."""

    bank_dir: Path
    lint_errors: list[str] = field(default_factory=list)
    signed: bool = False
    cert_subject: str | None = None
    cert_issuer: str | None = None
    error_detail: str | None = None

    def build(self) -> VerifyResult:
        ok = len(self.lint_errors) == 0 and (self.signed if _require_signed() else True)
        return VerifyResult(
            bank_dir=self.bank_dir,
            ok=ok,
            lint_errors=list(self.lint_errors),
            signed=self.signed,
            cert_subject=self.cert_subject,
            cert_issuer=self.cert_issuer,
            error_detail=self.error_detail,
        )


def _require_signed() -> bool:
    """Return True if OPEN_BANCA_REQUIRE_SIGNED_MAPS=1 is set in environment.

    Read once per call (not cached at module level) so tests can toggle the env
    between calls without reloading the module.
    """
    return os.environ.get("OPEN_BANCA_REQUIRE_SIGNED_MAPS", "0").strip() == "1"


class MapVerifier:
    """Runtime enforcer: combines MapLinter + cosign signature verification.

    Cosign check is opt-in via OPEN_BANCA_REQUIRE_SIGNED_MAPS=1.
    Linter check is always run.
    All attempts are emitted to the open_banca.audit logger.
    """

    def __init__(self) -> None:
        self._linter = MapLinter()
        self._require_signed = _require_signed  # callable for testability

    def verify(self, bank_dir: Path) -> VerifyResult:
        """Verify *bank_dir* (lint + optional cosign).

        Args:
            bank_dir: Path to a bank directory containing map.json and parser.json.

        Returns:
            VerifyResult with ok=True only if all required checks pass.
        """
        builder = _MutableVerifyResult(bank_dir=bank_dir)

        # Step 1: lint
        lint_result: LintResult = self._linter.lint(bank_dir)
        builder.lint_errors = [str(e) for e in lint_result.errors]

        lint_ok = lint_result.ok
        require_signed = self._require_signed()

        _AUDIT_LOG.info(
            "community_map.verify",
            extra={
                "bank_dir": str(bank_dir),
                "lint_ok": lint_ok,
                "lint_error_count": len(builder.lint_errors),
                "require_signed": require_signed,
            },
        )

        if not lint_ok:
            _AUDIT_LOG.warning(
                "community_map.lint_failed",
                extra={
                    "bank_dir": str(bank_dir),
                    "errors": builder.lint_errors,
                },
            )

        # Step 2: cosign verification
        map_path = bank_dir / "map.json"
        sig_path = bank_dir / "map.json.sig"
        cert_path = bank_dir / "map.json.crt"

        if require_signed:
            cosign_ok, cert_subject, cert_issuer, detail = _verify_cosign(
                map_path=map_path,
                sig_path=sig_path,
                cert_path=cert_path,
            )
            builder.signed = cosign_ok
            builder.cert_subject = cert_subject
            builder.cert_issuer = cert_issuer
            builder.error_detail = detail if not cosign_ok else None

            _AUDIT_LOG.info(
                "community_map.cosign_verify",
                extra={
                    "bank_dir": str(bank_dir),
                    "signed": cosign_ok,
                    "cert_subject": cert_subject,
                    "cert_issuer": cert_issuer,
                    "detail": detail,
                },
            )

            if not cosign_ok:
                _AUDIT_LOG.error(
                    "community_map.unsigned_rejected",
                    extra={
                        "bank_dir": str(bank_dir),
                        "reason": detail,
                    },
                )
        else:
            # Informational check — set signed flag but do not fail on absence
            builder.signed = sig_path.exists() and cert_path.exists()

        return builder.build()


def _verify_cosign(
    map_path: Path,
    sig_path: Path,
    cert_path: Path,
) -> tuple[bool, str | None, str | None, str | None]:
    """Run `cosign verify-blob` and parse its output.

    Returns:
        (success, cert_subject, cert_issuer, detail_message)
    """
    if not sig_path.exists():
        return False, None, None, f"Signature file not found: {sig_path}"
    if not cert_path.exists():
        return False, None, None, f"Certificate file not found: {cert_path}"
    if not map_path.exists():
        return False, None, None, f"Map file not found: {map_path}"

    cmd = [
        "cosign",
        "verify-blob",
        "--certificate-identity-regexp",
        _CERT_IDENTITY_REGEXP,
        "--certificate-oidc-issuer",
        _GITHUB_OIDC_ISSUER,
        "--certificate",
        str(cert_path),
        "--signature",
        str(sig_path),
        str(map_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return False, None, None, "cosign binary not found in PATH"
    except subprocess.TimeoutExpired:
        return False, None, None, "cosign verify-blob timed out after 30s"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, None, None, f"cosign verify-blob failed: {detail}"

    # Parse cert subject/issuer from stdout (cosign emits JSON on success with --output-file
    # or plain text — we extract what we can from stderr/stdout)
    output = (proc.stdout + proc.stderr).strip()
    cert_subject = _extract_field(output, "Certificate identity:")
    cert_issuer = _extract_field(output, "Certificate OIDC issuer:")

    return True, cert_subject, cert_issuer, output or "OK"


def _extract_field(text: str, prefix: str) -> str | None:
    """Extract a field value following *prefix* in *text*."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip() or None
    return None
