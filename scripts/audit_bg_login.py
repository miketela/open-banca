#!/usr/bin/env python3
"""Auditoría del login BG: validación Angular, estrategias de input, red y consola.

Uso:
  source .env
  export OPEN_BANCA_DB_PATH="$(pwd)/open_banca.db"
  uv run python scripts/audit_bg_login.py           # visible, slow_mo
  uv run python scripts/audit_bg_login.py --headless

Salida: tmp/bg_login_audit/report.json + screenshots por estrategia.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FRAME = "#bel-desktop #inbank"
OUT_DIR = Path(__file__).resolve().parents[1] / "tmp" / "bg_login_audit"


@dataclass
class FieldState:
    input_class: str = ""
    input_valid: bool | None = None
    btn_disabled: bool | None = None
    answer_count: int = 0
    password_count: int = 0
    form_snippet: str = ""


@dataclass
class StrategyResult:
    name: str
    success: bool
    field_before: dict[str, Any] = field(default_factory=dict)
    field_after_fill: dict[str, Any] = field(default_factory=dict)
    field_after_submit: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    network_events: list[dict[str, Any]] = field(default_factory=list)
    console_events: list[dict[str, Any]] = field(default_factory=list)


def _load_username() -> str:
    from open_banca_storage import Argon2idKeyDerivation, ConnectionPool, SecretVault, migrate

    passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE", "")
    db_path = Path(os.environ.get("OPEN_BANCA_DB_PATH", "open_banca.db"))
    if not passphrase:
        raise SystemExit("Falta OPEN_BANCA_MASTER_PASSPHRASE en .env")
    pool = ConnectionPool(
        db_path=db_path,
        passphrase=passphrase,
        key_derivation=Argon2idKeyDerivation(),
    )
    conn = pool.get()
    migrate(conn)
    vault = SecretVault(pool=pool, master_passphrase=passphrase)
    try:
        return vault.fetch_credential("personal:username")
    finally:
        pool.close()


def _read_field_state(fl: Any) -> FieldState:
    inp = fl.locator("#txtLoginD").first
    btn = fl.locator("button.btningresar").first
    state = FieldState()
    try:
        state.input_class = inp.evaluate("e => e.className") or ""
        state.input_valid = bool(inp.evaluate("e => e.validity.valid"))
        state.btn_disabled = btn.is_disabled() if fl.locator("button.btningresar").count() else None
    except Exception:
        pass
    state.answer_count = fl.locator("#answer").count()
    state.password_count = fl.locator("input[type=password]").count()
    try:
        if fl.locator("form").count():
            state.form_snippet = (fl.locator("form").first.inner_text(timeout=2_000) or "")[:400]
    except Exception:
        pass
    return state


def _open_login_widget(page: Any) -> Any:
    page.goto("https://www.bgeneral.com/", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)
    page.locator("a.cli_action_button").first.click(force=True, timeout=5_000)
    page.locator("button.dropbtn").nth(0).click(timeout=15_000)
    page.wait_for_timeout(1_500)
    return page.frame_locator(FRAME)


def _attach_observers(page: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    network: list[dict[str, Any]] = []
    console: list[dict[str, Any]] = []

    def on_request(req: Any) -> None:
        url = req.url
        if any(x in url for x in ("bgeneral", "bel", "login", "segura", "inbank")):
            network.append(
                {
                    "t": datetime.now(UTC).isoformat(),
                    "kind": "request",
                    "method": req.method,
                    "url": url[:200],
                }
            )

    def on_response(resp: Any) -> None:
        url = resp.url
        if any(x in url for x in ("bgeneral", "bel", "login", "segura", "inbank")):
            network.append(
                {
                    "t": datetime.now(UTC).isoformat(),
                    "kind": "response",
                    "status": resp.status,
                    "url": url[:200],
                }
            )

    def on_console(msg: Any) -> None:
        if msg.type in ("error", "warning"):
            console.append(
                {
                    "t": datetime.now(UTC).isoformat(),
                    "type": msg.type,
                    "text": (msg.text or "")[:300],
                }
            )

    def on_page_error(err: Any) -> None:
        console.append(
            {
                "t": datetime.now(UTC).isoformat(),
                "type": "pageerror",
                "text": str(err)[:300],
            }
        )

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    return network, console


def _strategy_fill_tab(fl: Any, page: Any, username: str) -> None:
    inp = fl.locator("#txtLoginD").first
    inp.click()
    inp.fill(username)
    inp.press("Tab")
    page.wait_for_timeout(400)


def _strategy_press_sequentially_enter(fl: Any, page: Any, username: str) -> None:
    inp = fl.locator("#txtLoginD").first
    inp.click()
    inp.press_sequentially(username, delay=40)
    page.wait_for_timeout(300)
    inp.press("Enter")


def _strategy_dispatch_events(fl: Any, page: Any, username: str) -> None:
    inp = fl.locator("#txtLoginD").first
    inp.fill(username)
    inp.dispatch_event("input")
    inp.dispatch_event("change")
    inp.blur()
    page.wait_for_timeout(400)


def _strategy_js_native_events(fl: Any, page: Any, username: str) -> None:
    fl.locator("#txtLoginD").first.evaluate(
        """(el, value) => {
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
    }""",
        username,
    )
    page.wait_for_timeout(400)


STRATEGIES: list[tuple[str, Any]] = [
    ("fill_tab", _strategy_fill_tab),
    ("press_sequentially_enter", _strategy_press_sequentially_enter),
    ("dispatch_events", _strategy_dispatch_events),
    ("js_native_events", _strategy_js_native_events),
]


def _run_strategy(
    pw: Any,
    *,
    name: str,
    apply_input: Any,
    username: str,
    headless: bool,
    slow_mo: int,
    wait_after_submit_ms: int,
) -> StrategyResult:
    from playwright.sync_api import sync_playwright

    result = StrategyResult(name=name, success=False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot_dir = OUT_DIR / name
    shot_dir.mkdir(exist_ok=True)

    browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    net, con = _attach_observers(page)

    try:
        fl = _open_login_widget(page)
        result.field_before = asdict(_read_field_state(fl))

        apply_input(fl, page, username)
        result.field_after_fill = asdict(_read_field_state(fl))

        btn = fl.locator("button.btningresar").first
        if result.field_after_fill.get("btn_disabled"):
            # intentar click igual por si Angular no actualizó el atributo
            btn.click(force=True, timeout=5_000)
        else:
            btn.click(timeout=10_000)

        page.wait_for_timeout(wait_after_submit_ms)
        after = _read_field_state(fl)
        result.field_after_submit = asdict(after)
        result.success = after.answer_count > 0 or after.password_count > 0

        page.screenshot(path=str(shot_dir / "after_submit.png"))
        if not result.success:
            page.screenshot(path=str(shot_dir / "failure.png"), full_page=True)
    except Exception as exc:
        result.error = str(exc)
        try:
            page.screenshot(path=str(shot_dir / "error.png"), full_page=True)
        except Exception:
            pass
    finally:
        result.network_events = net[-40:]
        result.console_events = con[-20:]
        browser.close()

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Sin ventana visible")
    parser.add_argument("--slow-mo", type=int, default=0 if "--headless" in sys.argv else 150)
    parser.add_argument("--wait-after-submit-ms", type=int, default=12_000)
    parser.add_argument(
        "--strategies",
        nargs="*",
        default=[s[0] for s in STRATEGIES],
        help="Subconjunto de estrategias a probar",
    )
    args = parser.parse_args()

    username = _load_username()
    print(f"Usuario cargado desde vault (len={len(username)})")

    from playwright.sync_api import sync_playwright

    results: list[StrategyResult] = []
    with sync_playwright() as pw:
        for name, fn in STRATEGIES:
            if name not in args.strategies:
                continue
            print(f"\n── Estrategia: {name} ──")
            r = _run_strategy(
                pw,
                name=name,
                apply_input=fn,
                username=username,
                headless=args.headless,
                slow_mo=args.slow_mo,
                wait_after_submit_ms=args.wait_after_submit_ms,
            )
            results.append(r)
            ok = "OK" if r.success else "FAIL"
            after = r.field_after_submit
            print(
                f"  {ok}  #answer={after.get('answer_count', 0)} "
                f"password={after.get('password_count', 0)} "
                f"btn_disabled={after.get('btn_disabled')}"
            )
            if r.error:
                print(f"  error: {r.error}")
            if r.console_events:
                print(f"  console ({len(r.console_events)} msgs)")

    winners = [r.name for r in results if r.success]
    report = {
        "audited_at": datetime.now(UTC).isoformat(),
        "headless": args.headless,
        "winners": winners,
        "recommended": winners[0] if winners else None,
        "strategies": [asdict(r) for r in results],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nReporte: {report_path}")
    if winners:
        print(f"Estrategia ganadora: {winners[0]}")
        return 0
    print("Ninguna estrategia mostró #answer ni password — revisar screenshots en tmp/bg_login_audit/")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
