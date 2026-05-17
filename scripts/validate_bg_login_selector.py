#!/usr/bin/env python3
"""Abre BG en Chromium visible y reporta selectores del botón de login."""
from __future__ import annotations

import json
import sys
from pathlib import Path

CANDIDATES = [
    "#btn-login",
    "button#btn-login",
    "[id='btn-login']",
    "a[href*='banca']",
    "a[href*='login']",
    "button:has-text('Banca')",
    "button:has-text('Ingresar')",
    "a:has-text('Banca en Línea')",
    "a:has-text('Banca en linea')",
    ".btn-ingresar",
    "#lw_bel",
    "[data-testid*='login']",
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    out_dir = Path(__file__).resolve().parents[1] / "tmp" / "bg_login_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot = out_dir / "homepage.png"

    results: list[dict[str, object]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        print("Navegando a https://www.bgeneral.com/ …")
        page.goto("https://www.bgeneral.com/", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(5_000)

        for sel in CANDIDATES:
            loc = page.locator(sel)
            count = loc.count()
            visible = False
            text = ""
            if count > 0:
                try:
                    visible = loc.first.is_visible(timeout=2_000)
                    text = (loc.first.inner_text(timeout=2_000) or "").strip()[:80]
                except Exception:
                    pass
            results.append(
                {"selector": sel, "count": count, "visible": visible, "text": text}
            )
            mark = "✓" if visible else ("?" if count else "·")
            print(f"  {mark} {sel!r} count={count} visible={visible} text={text!r}")

        # Heurística: enlaces/botones con "banca" o "ingres"
        print("\nCandidatos por texto (primeros 15):")
        for expr in [
            "a:visible",
            "button:visible",
            "[role='button']:visible",
        ]:
            for el in page.locator(expr).all()[:40]:
                try:
                    t = (el.inner_text() or "").strip()
                    if not t or len(t) > 60:
                        continue
                    low = t.lower()
                    if "banca" in low or "ingres" in low or "login" in low:
                        tag = el.evaluate("e => e.tagName")
                        eid = el.evaluate("e => e.id || ''")
                        cls = el.evaluate("e => e.className || ''")
                        print(f"  - <{tag.lower()}> id={eid!r} class={str(cls)[:60]!r} text={t!r}")
                except Exception:
                    continue

        page.screenshot(path=str(screenshot), full_page=False)
        print(f"\nScreenshot: {screenshot}")
        print("Cierra la ventana del browser o espera 15s …")
        page.wait_for_timeout(15_000)
        browser.close()

    (out_dir / "selectors.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    hits = [r for r in results if r.get("visible")]
    if hits:
        print("\nRecomendado para map.json:", hits[0]["selector"])
        return 0
    print("\nNingún candidato visible — revisa screenshot y actualiza map manualmente.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
