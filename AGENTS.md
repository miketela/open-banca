# AGENTS.md

## Cursor Cloud specific instructions

### Current state (v0)

This repository is **documentation-only** — it contains 46 design documents, ADRs, and architecture specs but **zero source code, zero package manifests, and zero runnable services**. The README confirms: `v0 — diseño y documentación`.

### Planned stack (for when implementation begins)

| Area | Technology |
|------|------------|
| Language | Python 3.12+ |
| Package manager | uv |
| Lint | Ruff |
| Type checker | Pyrefly |
| Web framework | FastAPI |
| Monorepo | Nx + `@nxlv/python` |
| Workflow engine | Temporal |
| Database | SQLite + sqlcipher |
| Browser automation | Playwright |
| Containerization | Docker Compose |

See `docs/00-overview.md` for the full stack table and `docs/05-operations/deployment.md` for the planned container topology.

### What you can do now

- **Lint docs**: `markdownlint '**/*.md' --ignore node_modules --disable MD013 MD060 MD033` — disabling line-length and table-style rules is recommended since the Spanish prose docs use long lines and compact table pipes.
- **Read docs**: The entry point is `docs/README.md`, which indexes all 46 documents by category with a recommended reading order.

### What does NOT exist yet

- No `pyproject.toml`, `package.json`, `requirements.txt`, or any dependency manifest.
- No `Dockerfile`, `docker-compose.yml`, or CI/CD configuration.
- No Python source code, tests, or scripts.
- No runnable application or services.

### When code lands

Once implementation begins (tracked in `.taskmaster/`), the dev setup will likely require:

1. `uv sync` — Python dependencies
2. `npm install` — Nx monorepo tooling
3. `playwright install chromium` — browser for scraper
4. Docker Compose — Temporal + Postgres + optional Langfuse
5. LLM API keys (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`) for agent components

Refer to `docs/05-operations/testing.md` for the planned test strategy and CI matrix.
