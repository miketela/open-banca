# ADR-0007 — Excel parser declarativo (DSL whitelisted)

## Context

El componente que transforma el Excel descargado del banco en records normalizados al schema canónico debe correr **dentro del sandbox por job** (Docker efímero, network policy con drop-all + allowlist por banco — ver ADR-0009). El parser tiene que ser:

- Configurable por banco (cada banco tiene su propio formato Excel).
- Distribuible vía repositorio open-source con contribuciones de la comunidad (`community/` folder).
- Reviewable por terceros sin asumir background Python deep.
- Versionable y diffeable en git con cambios incrementales.
- Inseguro de escapar el sandbox — los maps no oficiales son superficie de supply chain.

Dos opciones reales sobre la mesa:

1. **Parser como Python module por banco** con allowlist de imports + ejecutor sandboxed (subprocess + seccomp + bind mounts read-only).
2. **DSL declarativo** (JSON) con set finito de helpers whitelisted, ejecutado por un engine único que vive en el core.

## Decision

Adoptamos la **opción 2: DSL declarativo en `parser.json`** con set cerrado de helpers (`parse_date`, `extract_regex`, `normalize_amount`, `lookup_table`, `coalesce`, `trim`, `to_upper`, `to_lower`, `concat`, `negate_if`, `fixed`, `compute_hash`).

Cada `parser.json` describe sheets, headers, columnas y pipelines de transformación por celda. El engine único en el core lee, valida contra JSON Schema, y ejecuta. **No hay punto de extensión runtime para helpers** — agregar uno requiere PR al core con tests + doc.

Detalle del formato y helpers en [`../02-components/parser.md`](../02-components/parser.md).

## Consequences

**Positivas**:

- **Sandbox-friendly**: `parser.json` es data, no código. El engine ejecuta interpretación; nadie carga módulos arbitrarios. La superficie de RCE en el path de parsing es **el engine mismo** (auditado, en el core), no los maps de terceros.
- **Community-safe**: un fork malicioso publicando un `community/banco_xyz/parser.json` no puede ejecutar código arbitrario en deploys que lo importen — sólo puede definir transformaciones declarativas con helpers ya validados.
- **Diffeable y reviewable**: PRs cambian JSON. Reviewer no necesita leer Python.
- **Validable estáticamente**: linter en CI valida que `transformations` referencian helpers existentes con tipos compatibles.
- **Versionable**: tags git (`bank/banco_general/v1.3.0`) sellan map+parser juntos.
- **Testeable trivialmente**: dado un Excel fixture y un `parser.json`, el output es determinístico.

**Negativas / costos**:

- **Expresividad limitada**: bancos con lógica peculiar (ej. inferir signo de transacción de varias columnas combinadas con regex anidado) requieren extender el set de helpers. Eso es un PR al core, no una solución local.
- **Curva inicial del DSL**: contribuyentes deben aprender el formato (mitigado por docs + ejemplos).
- **Engine es punto único de complejidad**: bug en el engine afecta todos los bancos. Mitigado por test coverage exhaustivo (property tests sobre fixtures de todos los bancos).
- **Helpers cerrados**: si un banco aparece con caso extremo, hay un periodo de espera hasta que el helper esté en el core. Aceptable para un proyecto que mantiene un set limitado de bancos PA en v1.

## Alternatives considered

### Alt 1 — Python module por banco con sandbox subprocess

Cada banco trae un `parser.py` con función `parse(excel_path) -> list[Transaction]`. El runtime lo ejecuta en subprocess con seccomp + bind mounts read-only + sin red.

**Por qué se rechazó**:

- Superficie de ataque mucho mayor: incluso con seccomp, un parser malicioso puede hacer DoS, leer otras secciones del filesystem montado, abrir conexiones a APIs LLM (que están en allowlist del sandbox), exfiltrar via LLM call.
- Reviewers de PRs tienen que leer Python con eye crítico de security. No escala con contribuciones comunitarias.
- Cambios de versión Python afectan parsers; rompe el contrato de comunidad.
- Diffs menos legibles.

**Cuándo reconsiderar**: si v2 requiere lógica que el DSL no puede expresar y agregar un helper "compute" se vuelve pendiente recurrente, evaluar embebido de un lenguaje script seguro (Lua sandboxed, Wasm, Rhai). No Python.

### Alt 2 — JSONata / JMESPath sobre el Excel pre-parseado

JSONata/JMESPath son lenguajes de query JSON con poder transformacional. Se podría pre-parsear el Excel a JSON y aplicar query.

**Por qué se rechazó**: pierde claridad por celda, no mapea bien a estructura tabular Excel, y los implementadores de cada lenguaje añaden dependencia + superficie. El DSL custom es más simple para nuestro use case.

### Alt 3 — Parser como código Python en el core (hard-coded por banco)

`parser_banco_general.py` vive en el core. Cada banco nuevo es un PR al repo principal.

**Por qué se rechazó**: anula el goal de comunidad (gente sumando bancos sin tocar el core). Concentra todo el riesgo de mantenimiento en el equipo del core. No escala más allá de 5-10 bancos.

## Status

Accepted (2026-05-09).
