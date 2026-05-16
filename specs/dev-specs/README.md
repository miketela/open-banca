# Dev-Specs

Especificaciones técnicas detalladas por Historia de Usuario (HU). Una por HU en GitHub Issues.

## Convención

- Un archivo por HU: `HU<NN>-<slug>.md` (`NN` = 2 dígitos, `slug` = kebab-case).
- Cada archivo enlaza a su GitHub Issue en la cabecera.
- Cada GitHub Issue enlaza al archivo aquí en su body.
- **Source of truth técnico** vive aquí (versionado, diffable, code-reviewable).
- La issue queda como vista navegable + tracking de status.

## Estructura del spec

```
# HU<NN> — <título>

> Issue: #<num>
> Branch: <task-NN-slug | TBD>
> Estado: <draft|in-progress|review|done>
> Depende de: <HUNN, taskmaster:<id>, plan-fase:<id>>

## Contexto
Por qué existe esta HU. Vincula al PRD / ADR / plan relevante.

## Acceptance Criteria
- [ ] AC1
- [ ] AC2

## Plan técnico
Archivos a tocar, comandos, decisiones de diseño.

## Tests
Qué validamos y cómo. Comandos exactos.

## Riesgos
Bloqueadores o trade-offs.

## Definition of Done
Checklist explícito de cierre.
```

## Índice de HUs

| HU | Título | Issue | Estado | Depende de |
|----|--------|-------|--------|------------|
| HU01 | Mapper en vivo a Banco General + HAR fixture | TBD | draft | plan-fase:F0-F2 |
| HU02 | Deploy local docker-compose con `.env` real | TBD | draft | HU01 |
| HU03 | Smoke E2E real Banco General | TBD | draft | HU02 |
| HU04 | USER-TEST 1: Foundation review | TBD | draft | taskmaster:7 |
| HU05 | USER-TEST 2: Scraper runner E2E | TBD | draft | taskmaster:12 |
| HU06 | USER-TEST 3: Banco General piloto | TBD | draft | HU03 |
| HU07 | USER-TEST 4: Self-healing flow | TBD | draft | taskmaster:21 |
| HU08 | USER-TEST 5: Operations + security | TBD | draft | taskmaster:23-28 |
| HU09 | v1.0.0 release: E2E + stress + docs + tag | TBD | draft | HU04-HU08 |
| HU10 | USER-TEST 6: v1.0.0 release validation | TBD | draft | HU09 |

> El campo "Issue" se actualiza después de `gh issue create`.
> El campo "Branch" se actualiza después de `gh issue develop`.
