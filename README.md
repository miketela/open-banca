# open-banca

API self-hosted, open source, para acceder a información financiera de bancos de Panamá vía web scraping asistido por agentes de IA.

> **Por qué existe**: Panamá no tiene Open Banking. Acceder a tus propios datos financieros requiere entrar a la web del banco y descargar reportes manualmente. `open-banca` automatiza ese proceso y lo expone como API. La meta secundaria es **presión institucional** — demostrar que la información que los bancos retienen ya es accesible al usuario, sólo está mal entregada.

## Estado

`v0` — diseño y documentación. Ver [`docs/`](./docs).

## Banco piloto

Banco General (Panamá).

## Lectura recomendada

1. [`docs/00-overview.md`](./docs/00-overview.md) — visión, scope, no-goals.
2. [`docs/01-architecture/macro.md`](./docs/01-architecture/macro.md) — arquitectura macro.
3. [`docs/01-architecture/multi-agent.md`](./docs/01-architecture/multi-agent.md) — orquestación de agentes.
4. [`docs/adr/`](./docs/adr/) — decisiones arquitectónicas registradas.

## Licencia

AGPL-3.0 — cualquier servicio derivado debe permanecer abierto. Es deliberado.
