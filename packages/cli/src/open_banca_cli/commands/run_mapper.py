"""run-mapper command — invoke MapperAgent to produce a BankMap for a target bank.

This command is gated behind the OPEN_BANCA_LIVE_MAPPER=1 environment variable.
When the env var is absent, it prints a dry-run plan and exits 0 without touching
any browser or LLM.  This keeps CI clean and token-free.

Usage::

    # Dry-run plan (no browser, no API calls):
    open-banca run-mapper --bank banco_general --credential personal

    # Live mapping (requires ANTHROPIC_API_KEY + running Chromium):
    OPEN_BANCA_LIVE_MAPPER=1 open-banca run-mapper --bank banco_general --credential personal

    # Live + raw HAR (sanitized automatically after the run):
    OPEN_BANCA_LIVE_MAPPER=1 open-banca run-mapper --bank banco_general --credential personal --capture-har
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# Hard-coded bank URL registry for known pilot banks.
_BANK_START_URLS: dict[str, str] = {
    "banco_general": "https://www.bgeneral.com/",
}

# Output path relative to the package root for each known bank.
_BANK_MAP_OUTPUT: dict[str, str] = {
    "banco_general": "packages/banks/banco_general/map.json",
}

# Cost / wallclock caps (must match MapperAgent defaults: $0.50, 5 min).
_COST_CAP_USD: float = 0.50
_WALLCLOCK_SECONDS: float = 300.0


def _find_repo_root() -> Path:
    """Walk upward from CWD to find the repo root (contains pyproject.toml with [tool.uv.workspace])."""
    here = Path.cwd()
    for candidate in [here, *here.parents]:
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            if "[tool.uv.workspace]" in content:
                return candidate
    # Fallback: assume CWD is repo root
    return here


def _har_raw_dir(repo_root: Path, bank: str) -> Path:
    return (repo_root / f"packages/banks/{bank}/fixtures/har/raw").resolve()


def _har_raw_path(repo_root: Path, bank: str, override: Path | None) -> Path:
    if override is not None:
        path = override if override.is_absolute() else repo_root / override
        return path.resolve()
    return _har_raw_dir(repo_root, bank) / "mapper_run.har"


def _har_sanitized_path(repo_root: Path, bank: str) -> Path:
    return repo_root / f"packages/banks/{bank}/fixtures/har/sanitized/mapper_run.har"


def _ensure_har_path_under_raw_dir(repo_root: Path, bank: str, har_path: Path) -> None:
    """Raw HAR must live under fixtures/har/raw/ (gitignored) to avoid accidental commits."""
    raw_dir = _har_raw_dir(repo_root, bank)
    resolved = har_path.resolve()
    try:
        resolved.relative_to(raw_dir)
    except ValueError as exc:
        console.print(
            "[bold red]Error: --capture-har-path must be inside[/bold red]\n"
            f"  {raw_dir}\n"
            f"  Got: {resolved}\n"
            "  Paths outside fixtures/har/raw/ are not gitignored and may contain live credentials."
        )
        raise typer.Exit(code=1) from exc


def run_mapper(
    bank: Annotated[
        str,
        typer.Option("--bank", help="Bank identifier, e.g. banco_general"),
    ],
    credential: Annotated[
        str,
        typer.Option(
            "--credential",
            help="Credential label registered via register-credentials, e.g. personal",
        ),
    ] = "personal",
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Print invocation plan without executing (default: auto-detected from env)",
        ),
    ] = False,
    capture_har: Annotated[
        bool,
        typer.Option(
            "--capture-har",
            help="Live: record raw HAR under packages/banks/<bank>/fixtures/har/raw/; "
            "then write sanitized fixtures/har/sanitized/mapper_run.har.",
        ),
    ] = False,
    capture_har_path: Annotated[
        Path | None,
        typer.Option(
            "--capture-har-path",
            help="Override raw HAR filename under fixtures/har/raw/ (implies capture when set).",
            path_type=Path,
        ),
    ] = None,
    keep_raw_har: Annotated[
        bool,
        typer.Option(
            "--keep-raw",
            help="After sanitizing, keep the raw HAR on disk (default: delete raw after sanitize).",
        ),
    ] = False,
) -> None:
    """Run the MapperAgent to produce a BankMap for BANK.

    The agent browses the bank's website using browser-use + Claude vision
    and generates a map.json describing the scrape steps.

    Gate: set OPEN_BANCA_LIVE_MAPPER=1 to enable live execution.
    Without it (or with --dry-run), this command prints the plan and exits 0.

    Requirements (live mode):
      - ANTHROPIC_API_KEY env var must be set.
      - Chromium must be installed: uv run playwright install chromium
      - Cost cap: $0.50 USD per run.
      - Wallclock cap: 300 seconds (5 min).

    Output: packages/banks/<bank>/map.json
    """

    live_mode = os.environ.get("OPEN_BANCA_LIVE_MAPPER", "").strip() == "1" and not dry_run

    bank_url = _BANK_START_URLS.get(bank)
    if bank_url is None:
        console.print(
            f"[yellow]Warning: no hard-coded start URL for bank {bank!r}. "
            f"MapperAgent will derive: https://www.{bank.replace('_', '-')}.com[/yellow]"
        )
        bank_url = f"https://www.{bank.replace('_', '-')}.com"

    repo_root = _find_repo_root()
    map_output_rel = _BANK_MAP_OUTPUT.get(bank, f"packages/banks/{bank}/map.json")
    map_output_path = repo_root / map_output_rel

    want_har = capture_har or capture_har_path is not None
    har_raw_path: Path | None = None
    har_sanitized_path: Path | None = None
    if want_har:
        har_raw_path = _har_raw_path(repo_root, bank, capture_har_path)
        _ensure_har_path_under_raw_dir(repo_root, bank, har_raw_path)
        har_sanitized_path = _har_sanitized_path(repo_root, bank)

    if not live_mode:
        _print_dry_run_plan(
            bank,
            credential,
            bank_url,
            map_output_path,
            har_raw_path=har_raw_path,
            har_sanitized_path=har_sanitized_path,
        )
        raise typer.Exit(code=0)

    # ── Live mode ────────────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        console.print("[red]Error: ANTHROPIC_API_KEY env var is not set.[/red]")
        console.print("Set it before running in live mode:")
        console.print("  export ANTHROPIC_API_KEY=sk-ant-...")
        raise typer.Exit(code=1)

    console.print(
        Panel.fit(
            f"[bold green]MapperAgent — LIVE MODE[/bold green]\n"
            f"bank      : {bank}\n"
            f"credential: {credential}\n"
            f"start_url : {bank_url}\n"
            f"output    : {map_output_path}\n"
            f"cost_cap  : ${_COST_CAP_USD:.2f}\n"
            f"wallclock : {int(_WALLCLOCK_SECONDS)}s",
            title="open-banca run-mapper",
        )
    )

    bank_map_dict = asyncio.run(
        _run_live(
            bank,
            credential,
            bank_url,
            har_output_path=har_raw_path,
        )
    )

    # Write output
    map_output_path.parent.mkdir(parents=True, exist_ok=True)
    with map_output_path.open("w") as fh:
        json.dump(bank_map_dict, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    console.print(f"\n[green]map.json written:[/green] {map_output_path}")
    console.print(
        "[bold]Next step:[/bold] run dry_run_scraper to verify the map, "
        "then POST /scrape when ready."
    )

    if want_har and har_raw_path is not None:
        if har_raw_path.exists() and har_sanitized_path is not None:
            from open_banca_browser.har.sanitize import HARSanitizer

            HARSanitizer().sanitize_file(har_raw_path, har_sanitized_path)
            console.print(f"\n[green]Sanitized HAR written:[/green] {har_sanitized_path}")
            if not keep_raw_har:
                har_raw_path.unlink()
                console.print(
                    f"[dim]Raw HAR removed (use --keep-raw to retain for debug): {har_raw_path}[/dim]"
                )
        else:
            console.print(
                f"[yellow]Warning: raw HAR not found at {har_raw_path} — skip sanitize.[/yellow]"
            )


async def _run_live(
    bank: str,
    credential_label: str,
    bank_url: str,
    *,
    har_output_path: Path | None,
) -> dict[str, object]:
    """Execute the MapperAgent against the live bank website.

    Imports are lazy so that the heavy open-banca-llm dependency tree
    (browser-use, LiteLLM, Playwright) is only loaded in live mode,
    keeping CI import time near-zero.

    Returns:
        BankMap serialised as a plain dict (mode='json'), ready for json.dump.
    """
    try:
        from open_banca_llm.mapper.agent import MapperAgent  # type: ignore[import]
    except ImportError as exc:
        console.print(
            f"[red]Import error: {exc}[/red]\n"
            "The open-banca-llm package is required for live mapper execution.\n"
            "Run: uv sync --all-packages"
        )
        raise typer.Exit(code=1) from exc

    from open_banca_cli.vault_factory import open_vault_interactive

    # Resolve credentials from vault.
    # vault.fetch_credential(credential_ref) returns the plaintext secret.
    # credential_ref format: "<label>:<suffix>" (e.g. "personal:username")
    vault, _passphrase = open_vault_interactive()
    sensitive_data: dict[str, str] = {}

    with vault:
        for suffix in ("username", "password"):
            field_label = f"{credential_label}:{suffix}"
            try:
                # fetch_credential resolves by credential_ref stored during register-credentials
                value = vault.fetch_credential(field_label)
                sensitive_data[field_label] = value
            except Exception as exc:
                console.print(
                    f"[yellow]Warning: could not load credential {field_label!r}: {exc}[/yellow]"
                )

    if not sensitive_data:
        console.print("[red]No credentials resolved from vault. Register them first:[/red]")
        console.print(
            f"  uv run open-banca register-credentials --bank {bank} --label {credential_label}"
        )
        raise typer.Exit(code=1)

    agent = MapperAgent(  # type: ignore[misc]
        start_url_map={bank: bank_url},
        cost_cap_usd=_COST_CAP_USD,
        wallclock_cap_seconds=_WALLCLOCK_SECONDS,
        har_output_path=har_output_path,
    )

    console.print("[cyan]MapperAgent running — this may take up to 5 minutes...[/cyan]")
    t_start = time.monotonic()

    bank_map = await agent.map_bank(
        bank_id=bank,
        credential_ref=f"vault://{bank}/{credential_label}",
        sensitive_data=sensitive_data,
    )

    elapsed = time.monotonic() - t_start
    console.print(f"[green]MapperAgent completed in {elapsed:.1f}s[/green]")

    # Return as plain dict so caller can json.dump without needing BankMap import
    result: dict[str, object] = bank_map.model_dump(mode="json")  # type: ignore[union-attr]
    return result


def _print_dry_run_plan(
    bank: str,
    credential: str,
    bank_url: str,
    map_output_path: Path,
    *,
    har_raw_path: Path | None,
    har_sanitized_path: Path | None,
) -> None:
    """Print the dry-run plan — what would be invoked without actually doing it."""
    console.print(
        Panel.fit(
            "[bold yellow]DRY-RUN MODE[/bold yellow] (OPEN_BANCA_LIVE_MAPPER not set)\n"
            "No browser launched, no LLM calls, no API keys consumed.\n"
            "Set OPEN_BANCA_LIVE_MAPPER=1 to execute for real.",
            title="open-banca run-mapper",
        )
    )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Parameter", style="cyan")
    table.add_column("Value")
    table.add_row("bank", bank)
    table.add_row("credential_label", credential)
    table.add_row("start_url", bank_url)
    table.add_row("output_path", str(map_output_path))
    table.add_row("cost_cap_usd", f"${_COST_CAP_USD:.2f}")
    table.add_row("wallclock_cap_s", str(int(_WALLCLOCK_SECONDS)))
    table.add_row("model", "claude-sonnet-4-6 (via LiteLLM)")
    if har_raw_path is not None:
        table.add_row("har_raw (live)", str(har_raw_path))
    if har_sanitized_path is not None:
        table.add_row("har_sanitized (live)", str(har_sanitized_path))
    console.print(table)

    console.print("\n[bold]What would happen in live mode:[/bold]")
    steps = [
        f"1. Resolve credentials from encrypted vault (label: {credential}:username, {credential}:password)",
        "2. Instantiate MapperAgent (browser-use + LiteLLM + Claude vision)",
        "3. Launch Chromium headless browser",
        f"4. Navigate to {bank_url}",
        "5. Agent explores login flow, dashboard, accounts, date range selectors, Excel download",
        "6. Agent generates BankMap JSON describing all steps with CSS selectors",
        "7. Validate BankMap schema (BankMap.model_validate)",
        "8. Self-test: ScraperRunner dry-run validates structural correctness",
        f"9. Write validated map.json to: {map_output_path}",
    ]
    if har_raw_path is not None:
        steps.append(
            f"10. Record raw HAR to {har_raw_path} (--capture-har), "
            "sanitize to fixtures/har/sanitized/mapper_run.har (HARSanitizer), "
            "then delete raw unless --keep-raw"
        )
    for step in steps:
        console.print(f"  {step}")

    console.print(
        "\n[bold]To run for real:[/bold]\n"
        "  OPEN_BANCA_LIVE_MAPPER=1 uv run open-banca run-mapper "
        f"--bank {bank} --credential {credential}"
        + (" --capture-har" if har_raw_path is not None else "")
    )
    if har_raw_path is None:
        console.print(
            "\n[dim]Optional: add --capture-har to record a raw HAR during the live run "
            "(default path: packages/banks/<bank>/fixtures/har/raw/mapper_run.har); "
            "after the run, a sanitized copy is written under fixtures/har/sanitized/. "
            "Redact manually with: uv run python scripts/redact_har.py raw.har out.har[/dim]"
        )
    console.print(
        "\n[dim]Estimated cost: <$0.50 USD. Requires: ANTHROPIC_API_KEY, "
        "Chromium (playwright install chromium), internet access to bank.[/dim]"
    )
