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
| HU01 | Mapper en vivo a Banco General + HAR fixture | [#7](https://github.com/miketela/open-banca/issues/7) | draft | plan-fase:F0-F2 |
| HU02 | Deploy local docker-compose con `.env` real | [#8](https://github.com/miketela/open-banca/issues/8) | draft | HU01 |
| HU03 | Smoke E2E real Banco General | [#9](https://github.com/miketela/open-banca/issues/9) | draft | HU02 |
| HU04 | USER-TEST 1: Foundation review | [#10](https://github.com/miketela/open-banca/issues/10) | draft | taskmaster:7 |
| HU05 | USER-TEST 2: Scraper runner E2E | [#11](https://github.com/miketela/open-banca/issues/11) | draft | taskmaster:12 |
| HU06 | USER-TEST 3: Banco General piloto | [#12](https://github.com/miketela/open-banca/issues/12) | draft | HU03 |
| HU07 | USER-TEST 4: Self-healing flow | [#13](https://github.com/miketela/open-banca/issues/13) | draft | taskmaster:21 |
| HU08 | USER-TEST 5: Operations + security | [#14](https://github.com/miketela/open-banca/issues/14) | draft | taskmaster:23-28 |
| HU09 | v1.0.0 release: E2E + stress + docs + tag | [#15](https://github.com/miketela/open-banca/issues/15) | draft | HU04-HU08 |
| HU10 | USER-TEST 6: v1.0.0 release validation | [#16](https://github.com/miketela/open-banca/issues/16) | draft | HU09 |

> El campo "Branch" se actualiza después de `gh issue develop`.
