# ADR-0007 Amendment — DSL Hardening: re2, defusedxml, resource budgets

## Context

ADR-0007 adoptó un DSL declarativo (`parser.json`) para eliminar RCE vía Python arbitrario. Sin embargo, la auditoría de seguridad (docs/reviews/02-security-opus.md, P0-4) identificó tres vectores de DoS dentro del propio engine que procesan datos controlables por el map:

1. **ReDoS (CWE-1333)**: el helper `extract_regex` usa el módulo `re` de la stdlib Python, que implementa backtracking NFA. Un pattern cuadrático (p. ej. `(a+)+$`) puede hacer que una celda con input crafteado consuma tiempo de CPU indefinido, colgando el worker Temporal.

2. **Zip bomb / XML externo (CVE-2017-5992 / defusedxml advisories)**: `openpyxl` abre `.xlsx` (que son archivos ZIP + XML) sin restricciones. Un archivo con ratio de compresión extremo (zip bomb) o con entidades XML externas puede agotar memoria o disparar SSRF dentro del sandbox.

3. **Exhaustión de recursos sin budget (CWE-400)**: `lookup_table` acepta tablas de tamaño arbitrario. `concat` puede operar sobre un número ilimitado de refs. Sin caps explícitos, un map comunitario malicioso o corrupto puede provocar OOM o CPU spin que no es contenido por las resource limits del sandbox.

El DSL elimina RCE, pero sin un modelo de recursos no elimina DoS — que sigue siendo un vector válido de ataque en un entorno multi-tenant o con maps comunitarios no auditados.

## Decision

### 1. Migrar `extract_regex` a re2

Reemplazar el backend de `extract_regex` por `google/re2` via el binding Python (`re2` o `pyre2`). re2 implementa autómatas finitos deterministas (DFA/NFA without backtracking), garantizando complejidad O(n) en el largo del input independientemente del pattern.

- El linter agrega la regla **L13** (ver sección de consecuencias) que valida, en tiempo de CI, que cada `extract_regex` pattern en `parser.json` sea re2-compatible antes de que el map llegue al engine.
- Si re2 no puede compilar el pattern (features no soportadas como lookahead arbitrario), el linter rechaza el map con error descriptivo.
- Los patterns existentes en maps oficiales se validan y migran como parte de este amendment.

### 2. Envolver openpyxl con defusedxml para ingesta segura de Excel

- Configurar defusedxml para deshabilitar resolución de entidades externas antes de que openpyxl parsee el XML interno del `.xlsx`.
- Rechazar `.xlsx` con tamaño en disco > **50 MB** antes de abrir (guard en el engine, error `excel_too_large`).
- Rechazar `.xlsx` cuyo ratio de compresión ZIP exceda **100x** (heurístico zip bomb: tamaño descomprimido / tamaño comprimido). Medido sobre el ZIP header sin descomprimir completamente (streaming scan). Error `excel_zip_bomb_heuristic`.
- Estas validaciones son irrechazables — no hay flag para desactivarlas.

### 3. Hard caps de recursos por helper

Cada helper del engine tiene límites irrechazables:

| Helper | Cap |
|--------|-----|
| `extract_regex` | Timeout por celda: **100 ms** (cortocircuito via signal/thread timer). Pattern validado re2-compat en linter. |
| `parse_date` | Timeout por celda: **100 ms**. |
| `lookup_table` | Máximo **100 000 filas** en el map. Lookup via hash O(1); prohibidas búsquedas lineales. |
| `concat` | Máximo **50 refs** por invocación. Output truncado a **64 KB** por celda. |
| Sheet completo | Máximo **10 MB** de datos descomprimidos por sheet procesado (acumulado sobre todas las celdas). |
| Iteraciones por sheet | Máximo **10 000 filas** por sheet (configurable hasta ese límite; no override por map). |
| CPU por activity | El Temporal activity `ParseExcelActivity` corre con resource limit de contenedor; el budget per-helper es una defensa adicional en user-space. |

Si cualquier cap se excede, la activity aborta con error `resource_budget_exceeded` + detalle del helper y celda. El record parcial se descarta.

### 4. Linter: reglas L13 y L14

El linter de community maps recibe dos reglas nuevas:

| ID | Regla |
|----|-------|
| L13 | Cada `extract_regex` pattern debe ser compilable por re2 sin error. Patterns con lookahead/lookbehind sin límite de longitud son rechazados. |
| L14 | El tamaño total de todos los `lookup_table` maps en un `parser.json` no debe superar 1 MB (serializado en JSON). Esto complementa el cap en runtime (100 K filas). |

Estas reglas se suman a L01–L12 existentes. El conteo total pasa a **14 reglas** (referenciado como REQ-017 en el PRD).

## Consequences

**Positivas**:

- **ReDoS eliminado**: re2 garantiza linealidad O(n). CWE-1333 queda mitigado por construcción, no por política.
- **Zip bomb / XML externo mitigado**: defusedxml + caps de tamaño reducen el vector de CVE-2017-5992 (XML unsafe loading) y sus equivalentes en openpyxl.
- **DoS por exhaustión acotado**: caps hard en runtime + linter impiden que un map comunitario consuma recursos ilimitados. CWE-400 mitigado.
- **Detección temprana en CI**: L13/L14 detectan maps problemáticos antes del merge, sin coste de runtime.
- **Sin cambio en la API pública del DSL**: `extract_regex` sigue teniendo la misma interfaz; el cambio es de backend. Maps existentes válidos siguen funcionando si son re2-compatible (la gran mayoría lo son).

**Negativas / costos**:

- **Dependencia nueva**: `re2` (binding Python de google/re2) se suma al stack. Es una dependencia con binding C++; requiere que las imágenes Docker incluyan la librería compartida `libre2`. Mitigado: la imagen base Debian/Alpine tiene `libre2` en el repositorio oficial.
- **Features de re2 excluidas**: lookahead/lookbehind sin límite, backreferences, POSIX collating elements. En la práctica ningún bank map oficial usa estas features para extracción de columnas. Los casos que los necesiten requieren un helper más específico en el core.
- **Overhead de linter en CI**: re2 compile-check agrega ~50 ms por pattern al pipeline CI. Negligible dado el número de patterns por bank map (típicamente < 20).
- **Falsos positivos en zip bomb heuristic**: archivos con alta compresión legítima (spreadsheets con muchas celdas vacías) pueden acercarse al ratio 100x. En ese caso el banco o el operador ajusta en coordinación con el mantenedor; el cap es configurable a nivel del operador vía variable de entorno (no override por map).

## Alternatives considered

### Alt 1 — Timeout de signal SIGALRM sobre re stdlib

Aplicar `signal.alarm()` para limitar el tiempo del helper a 100 ms. No requiere cambio de librería.

**Por qué se rechazó**: SIGALRM no es seguro en entornos multi-thread (Python asyncio + Temporal). La señal interrumpe un thread arbitrario. Además, no previene patrones que consumen tiempo en compilación (antes del match). re2 es la solución correcta para el problema.

### Alt 2 — Sandboxear cada helper en subprocess separado

Correr `extract_regex` en un subprocess efímero con timeout de OS. Limita blast radius a nivel de proceso.

**Por qué se rechazó**: overhead de IPC por celda es prohibitivo (miles de celdas por sheet). El engine ya corre dentro del sandbox Docker per-job; un sub-sandbox por helper es defensiva redundante cara. re2 resuelve el problema sin overhead.

### Alt 3 — Permitir re stdlib con allowlist de patterns seguros

Mantener `re` pero exigir que cada pattern pase por una herramienta de análisis estático de complejidad (p. ej. `regexploit`, `rexploiter`).

**Por qué se rechazó**: el análisis estático de ReDoS no tiene cobertura completa — hay patterns que escapan a los analizadores actuales. re2 es una garantía constructiva, no una heurística.

## Referencias

- CWE-1333: Inefficient Regular Expression Complexity (ReDoS)
- CVE-2017-5992 / defusedxml advisories: XML unsafe loading en parsers basados en lxml/openpyxl
- CWE-400: Uncontrolled Resource Consumption
- ADR-0007: [`0007-declarative-excel-dsl.md`](./0007-declarative-excel-dsl.md)
- Componente parser: [`../02-components/parser.md`](../02-components/parser.md)
- Community maps / linter: [`../04-security/community-maps.md`](../04-security/community-maps.md)

## Status

Accepted (2026-05-10).
