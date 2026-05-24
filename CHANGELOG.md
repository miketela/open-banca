# Changelog

All notable changes to open-banca are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — draft (unreleased)

### Added

- `POST /credentials` — store encrypted bank credentials via SecretVault (Bearer auth).
- `GET /healthz` and `GET /readyz` health endpoints for Docker/orchestrator probes.
- `ParseExcelActivity` wired to `open-banca-parsing` engine with BG debit/credit support.
- `scripts/stress_test.py` skeleton for 100-job load validation (HU09).

### Changed

- API authentication documented as `Authorization: Bearer $API_KEY` (replaces `X-API-Key`).
- Production API port standardized to **8080** in Docker Compose.
- README and deployment docs aligned with CLI + API credential registration flows.

### Fixed

- Banco General `parser.json` amount resolution from separate debit/credit columns.
- Docker Compose healthcheck targets `/healthz`.

### Security

- Credentials stored with per-row AES-GCM via SecretVault; plaintext never returned in API responses.

### Known limitations (v1.0.0 draft)

- RemapBankWorkflow self-healing cycle pending full validation (HU07).
- Sandbox browser spawn in isolated container pending hardening (HU08).
- Live Banco General E2E smoke not yet signed off (HU03/HU06).

[1.0.0]: https://github.com/open-banca/open-banca/compare/v0.1.0...v1.0.0
