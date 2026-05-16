"""scripts/verify_bank_map.py — CLI helper for runtime cosign verification of bank maps.

Usage::

    python scripts/verify_bank_map.py <bank_dir> [--release-tag <tag>] [--org <github-org>]

Also importable as a library::

    from scripts.verify_bank_map import verify_bank_map
    ok, cert_subject = verify_bank_map(Path("packages/banks/banco_general"))

The script:
1. Looks for map.json.bundle (cosign bundle file) in bank_dir,
   or fetches it from the GitHub release if --release-tag is supplied.
2. Runs `cosign verify-blob` with GitHub OIDC keyless parameters.
3. Returns True/cert_subject on success, False/None on failure.

Exit codes: 0 = verified, 1 = failed/unsigned.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.request import urlretrieve

logger = logging.getLogger(__name__)

_GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
_DEFAULT_ORG = "open-banca"
_DEFAULT_CERT_IDENTITY_REGEXP = (
    "https://github.com/{org}/.+/.github/workflows/sign-bank-maps.yml@refs/tags/.*"
)


class VerifyOutcome(NamedTuple):
    """Result of a single verify_bank_map call."""

    ok: bool
    cert_subject: str | None
    cert_issuer: str | None
    detail: str


def verify_bank_map(
    bank_dir: Path,
    release_tag: str | None = None,
    org: str = _DEFAULT_ORG,
    github_repo: str | None = None,
) -> VerifyOutcome:
    """Verify a bank map's cosign signature.

    Args:
        bank_dir: Directory containing map.json (and optionally map.json.bundle).
        release_tag: If supplied, attempt to fetch the bundle from the GitHub release.
        org: GitHub org/user owning the repo (used in identity regexp).
        github_repo: Full repo name (org/repo). Defaults to ``{org}/open-banca``.

    Returns:
        VerifyOutcome namedtuple.
    """
    map_path = bank_dir / "map.json"
    bundle_path = bank_dir / "map.json.bundle"
    repo = github_repo or f"{org}/open-banca"

    if not map_path.exists():
        return VerifyOutcome(
            ok=False, cert_subject=None, cert_issuer=None, detail=f"map.json not found: {map_path}"
        )

    # Attempt to fetch bundle from GitHub release if not present locally
    if not bundle_path.exists() and release_tag:
        bundle_path = _fetch_bundle_from_release(
            map_path=map_path,
            bundle_path=bundle_path,
            repo=repo,
            release_tag=release_tag,
        )

    if not bundle_path or not bundle_path.exists():
        return VerifyOutcome(
            ok=False,
            cert_subject=None,
            cert_issuer=None,
            detail="Bundle file not found and no release tag provided for fetch",
        )

    cert_identity_regexp = _DEFAULT_CERT_IDENTITY_REGEXP.format(org=org)

    cmd = [
        "cosign",
        "verify-blob",
        "--bundle",
        str(bundle_path),
        "--certificate-identity-regexp",
        cert_identity_regexp,
        "--certificate-oidc-issuer",
        _GITHUB_OIDC_ISSUER,
        str(map_path),
    ]

    logger.debug("Running: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return VerifyOutcome(
            ok=False, cert_subject=None, cert_issuer=None, detail="cosign binary not found in PATH"
        )
    except subprocess.TimeoutExpired:
        return VerifyOutcome(
            ok=False,
            cert_subject=None,
            cert_issuer=None,
            detail="cosign verify-blob timed out after 30s",
        )

    output = (proc.stdout + proc.stderr).strip()

    if proc.returncode != 0:
        return VerifyOutcome(
            ok=False,
            cert_subject=None,
            cert_issuer=None,
            detail=f"cosign verify-blob failed: {output}",
        )

    # Extract certificate fields from output
    cert_subject = _extract_field(output, "Certificate identity:")
    cert_issuer = _extract_field(output, "Certificate OIDC issuer:")

    # Also try parsing bundle JSON for identity info
    if not cert_subject:
        cert_subject = _extract_from_bundle(bundle_path)

    return VerifyOutcome(
        ok=True, cert_subject=cert_subject, cert_issuer=cert_issuer, detail=output or "Verified OK"
    )


def _fetch_bundle_from_release(
    map_path: Path,
    bundle_path: Path,
    repo: str,
    release_tag: str,
) -> Path | None:
    """Attempt to download the bundle for *map_path* from a GitHub release."""
    # Construct the expected asset filename (relative path, slashes → underscores)
    asset_name = f"{map_path.name}.bundle"
    url = f"https://github.com/{repo}/releases/download/{release_tag}/{asset_name}"
    try:
        logger.info("Fetching bundle from: %s", url)
        urlretrieve(url, bundle_path)
        return bundle_path
    except Exception as exc:
        logger.warning("Could not fetch bundle from release: %s", exc)
        return None


def _extract_field(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip() or None
    return None


def _extract_from_bundle(bundle_path: Path) -> str | None:
    """Try to extract the cert identity from a cosign bundle JSON."""
    try:
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
        # cosign bundle format: {"mediaType": ..., "verificationMaterial": {...}, ...}
        # cert is in verificationMaterial.certificate.rawBytes (base64) or similar
        cert_chain = (
            data.get("verificationMaterial", {})
            .get("x509CertificateChain", {})
            .get("certificates", [])
        )
        if cert_chain:
            return "<certificate present — run cosign for full subject>"
    except Exception:
        pass
    return None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python scripts/verify_bank_map.py",
        description="Verify a bank map's cosign signature (GitHub OIDC keyless).",
    )
    p.add_argument("bank_dir", type=Path, help="Path to bank directory")
    p.add_argument(
        "--release-tag",
        default=None,
        help="GitHub release tag to fetch bundle from (e.g. bank-maps/v1.0.0)",
    )
    p.add_argument(
        "--org",
        default=os.environ.get("OPEN_BANCA_GITHUB_ORG", _DEFAULT_ORG),
        help="GitHub org/user (default: open-banca or OPEN_BANCA_GITHUB_ORG env)",
    )
    p.add_argument(
        "--repo",
        default=None,
        help="Full GitHub repo name org/repo (default: {org}/open-banca)",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(message)s")

    outcome = verify_bank_map(
        bank_dir=args.bank_dir,
        release_tag=args.release_tag,
        org=args.org,
        github_repo=args.repo,
    )

    if outcome.ok:
        print(f"VERIFIED {args.bank_dir}")
        if outcome.cert_subject:
            print(f"  Certificate subject: {outcome.cert_subject}")
        if outcome.cert_issuer:
            print(f"  Certificate issuer:  {outcome.cert_issuer}")
    else:
        print(f"FAILED {args.bank_dir}: {outcome.detail}", file=sys.stderr)

    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
