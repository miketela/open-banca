# HAR Fixtures — Banco General

Playwright HAR network recordings used by integration tests to replay bank
interactions without hitting live servers.

## Directory layout

```
har/
  raw/           (gitignored) raw recordings with real credentials
  sanitized/     anonymized, committable fixtures
  README.md      this file
  .gitignore     blocks raw/ from git
```

## Available fixtures

| File (sanitized/) | Scenario |
|---|---|
| `login_happy.har` | Successful login + dashboard |
| `login_otp_pending.har` | Flow paused waiting for OTP push |
| `login_otp_rejected.har` | User rejected OTP push |
| `download_savings.har` | Savings account download flow |
| `download_credit_card.har` | Credit card statement download |
| `selector_drift_v1.har` | Captured during bank UI redesign |
| `http_503.har` | Bank returning 503 (triggers retry) |

## Recording a new fixture

1. Run the scraper in debug mode against the live bank (requires real credentials
   in your environment — never checked in):

```bash
# From repo root
uv run python -c "
from pathlib import Path
from playwright.sync_api import sync_playwright
from open_banca_browser.har.record import record_har

def scrape(page):
    page.goto('https://www.bancogeneral.com')
    # ... perform the interaction ...

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    record_har(browser, Path('packages/banks/banco_general/fixtures/har/raw/my_flow.har'), scrape)
    browser.close()
"
```

2. Sanitize before committing:

```bash
uv run python -m open_banca_browser.har.sanitize \
  packages/banks/banco_general/fixtures/har/raw/my_flow.har \
  packages/banks/banco_general/fixtures/har/sanitized/my_flow.har
```

3. Verify no secrets leaked:

```bash
grep -r "SECRET_CANARY_VALUE\|Authorization: Bearer\|Cookie:" \
  packages/banks/banco_general/fixtures/har/sanitized/ && echo "SECRETS FOUND" || echo "Clean"
```

4. Commit only the `sanitized/` file.

## Using fixtures in tests

```python
from pathlib import Path
from open_banca_browser.har.replay import HARReplay

FIXTURE = Path("packages/banks/banco_general/fixtures/har/sanitized/login_happy.har")

def test_login_flow(page):  # sync Playwright page
    replay = HARReplay.from_file(FIXTURE)
    replay.install(page)
    page.goto("https://www.bancogeneral.com/login")
    # assertions...
```

## Security

- The `raw/` directory is gitignored and must NEVER be committed.
- The pre-commit hook rejects any staged `.har` file that still contains
  known secret patterns (Authorization headers, canary values, passwords).
- After sanitizing, run `gitleaks detect --no-git` against the sanitized file
  as a final check.
