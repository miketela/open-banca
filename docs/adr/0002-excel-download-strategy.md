# ADR-0002 — Excel-first: descargar reporte oficial del banco vs DOM scrape

## Context

Los bancos panameños, igual que muchos bancos latinoamericanos, ofrecen en su portal web un módulo de "Estado de cuenta" o "Movimientos" donde el usuario puede:

1. Filtrar por cuenta y rango de fechas.
2. Ver las transacciones renderizadas en HTML (típicamente paginadas con JS, lazy load, o tablas dinámicas).
3. **Descargar un reporte oficial en Excel (`.xlsx`) o PDF** con el mismo dataset, generado por el backend del banco.

El Excel y el HTML representan el mismo dato lógico, pero el Excel es:

- Generado por el banco (canónico, oficial, lo que el banco mismo entrega para conciliación).
- Estructurado (filas/columnas con tipos, no DOM con clases CSS).
- Independiente del rendering web (no se rompe si cambian frameworks JS).
- Estable a través de cambios cosméticos del portal.

El DOM HTML, en contraste, cambia con cada redesign, A/B test o cambio de framework. Selectores se rompen; tablas se paginan distinto; números se formatean para humanos (separadores, monedas, paréntesis para negativos) en lugar de para máquinas.

Banco General (piloto) ofrece descarga directa de Excel desde la página de movimientos. Banistmo, Caja de Ahorros y otros del mismo mercado también ofrecen Excel o CSV oficial.

## Decision

**El path canónico de captura es: navegar a la sección de movimientos, aplicar filtros, descargar el Excel oficial, y parsearlo con un DSL declarativo.**

- El `map.json` describe el flujo hasta el botón de descarga, no la lectura de tablas.
- El `parser.json` describe cómo interpretar las celdas del Excel: dónde está el header de la cuenta, dónde empiezan las filas de transacciones, qué columnas mapean a qué campos canónicos, qué transformaciones aplicar (fecha, monto, descripción).
- **No scraping de transacciones desde el DOM** en v1.

Excepciones donde sí miramos el DOM:

- Para llegar al Excel (clicks, formularios, esperar redirects).
- Para extraer balance/saldo si **no** está en el Excel descargado y sí en el DOM (caso por caso, declarado en `map.json`).
- Para detectar el flujo de OTP / Clave Móvil.

## Consequences

### Positivas

- **Robustez frente a cambios cosméticos**: redesigns que cambian CSS no nos rompen mientras el botón "Descargar Excel" siga existiendo y el formato del archivo no cambie.
- **Datos limpios**: el Excel oficial ya tiene tipos, sin formateo humano (montos como número, no `"B/. 1,234.56"`).
- **Conciliación con el banco**: el operador puede comparar nuestros datos con el mismo Excel que descargaría manualmente. Cero ambigüedad sobre qué es el "ground truth".
- **Performance**: una descarga reemplaza N requests de paginación.
- **Auditabilidad**: guardamos el `.xlsx` original; cualquier discrepancia en transactions canónicas se puede re-derivar.
- **Rate-limit friendly**: una descarga = un request al backend del banco. DOM scraping iterativo es más detectable.

### Negativas

- **Cada banco necesita su propio parser DSL** (`parser.json`). El formato Excel varía: número de filas de header, columnas, codificación de montos negativos, separación entre cuentas en el mismo archivo.
- **Si un banco no ofrece Excel**, la estrategia no aplica directamente. Se evalúa caso a caso: PDF parser, CSV, o (último recurso) DOM scrape.
- **Cambios de formato Excel sí nos rompen**, igual que cambios de DOM. Pero suceden con frecuencia mucho menor.
- **Storage adicional**: guardamos los Excel originales en un volumen del operador (con TTL configurable) para auditoría.

### Operativas

- El DSL del parser está whitelisted: sólo helpers permitidos (`parse_date`, `extract_regex`, `normalize_amount`, `coerce_decimal`, `parse_panama_date`, etc.). Sin Python arbitrario. Esto encaja con la regla de sandbox y con [ADR-0007](./0007-declarative-excel-dsl.md).
- Cada banco bajo `banks/<bank>/` incluye fixtures Excel reales **anonimizadas** para tests del parser.

## Alternatives Considered

### A — DOM scrape directo de transacciones

**Rechazada.** Razones:

- Frágil: cualquier cambio de selector, paginación o framework JS rompe el scraper.
- Datos sucios: formateo humano que hay que normalizar (separadores de miles, monedas, paréntesis para negativos, fechas en formatos locales).
- Más visible para detección anti-bot del banco: muchos requests, navegación entre páginas, scroll para lazy-load.
- No hay "ground truth" claro para auditar discrepancias.

### B — API privada del banco (reverse-engineered)

**Rechazada para v1.** Razones:

- Riesgo legal/contractual mayor: usar endpoints internos del backend puede violar ToS más explícitamente que descargar reportes pensados para el usuario.
- Frágil: APIs privadas cambian sin notice y sin estabilidad declarada.
- Detección: rate-limits, tokens CSRF, signatures.
- No descartada permanentemente; reevaluable por banco si Excel deja de funcionar.

### C — PDF en vez de Excel

**Rechazada como default.** Razones:

- PDF es más difícil de parsear de forma robusta (layout-driven, no row-driven).
- Bancos PA suelen ofrecer ambos; cuando ambos están disponibles, Excel es claramente superior.
- Reservada como fallback por banco si Excel no está disponible.

### D — Híbrido (DOM + Excel reconciliado)

**Rechazada para v1.** Complejidad alta sin beneficio claro mientras Excel funcione bien. Reevaluable si encontramos un banco donde el Excel está desactualizado vs el DOM.

## Status: Accepted (2026-05-09)
