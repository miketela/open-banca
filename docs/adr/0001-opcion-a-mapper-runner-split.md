# ADR-0001 — Opción A: separar Mapper (LLM) de Scraper Runner (sin LLM)

## Context

`open-banca` necesita extraer datos financieros de bancos panameños navegando sus webs. Las opciones que consideramos para la arquitectura del scraping son:

- **Opción A (split)**: un agente LLM con vision explora el banco una vez y emite un artefacto declarativo (`map.json` + `parser.json`); un runner determinístico (Playwright puro, sin LLM) ejecuta ese mapa en cada scrape.
- **Opción B (LLM in loop)**: el LLM con vision controla el browser en cada scrape, decidiendo en runtime qué selector usar, qué hacer ante un cambio, etc.
- **Opción C (manual maps)**: humanos escriben los `map.json` a mano; sin agente. Sólo herramientas de testing.

La decisión impacta: costo operativo por scrape, latencia, determinismo, debuggeo, replay-ability, y cómo respondemos cuando el banco cambia.

Bancos PA cambian sus webs con baja frecuencia (semanas/meses), no constantemente. La superficie real que necesita "inteligencia" es el primer mapeo y la reparación cuando rompe — no el día a día.

## Decision

Adoptamos **Opción A**: separación estricta entre Mapper (LLM, one-time o on-break) y Scraper Runner (Playwright puro, cada corrida).

- El **Mapper** (Claude Sonnet 4.6 con vision, sobre librería `browser-use`) explora el banco la primera vez y produce dos artefactos declarativos:
  - `map.json` — selectores, flujo de navegación, filtros, paso de descarga.
  - `parser.json` — DSL whitelisted (`parse_date`, `extract_regex`, `normalize_amount`) que transforma el `.xlsx` descargado en rows canónicas.
- El **Scraper Runner** consume esos artefactos en cada job. Es Playwright puro, sin llamadas LLM, sin código arbitrario fuera del DSL.
- Cuando el Runner detecta una ruptura (selector ausente, schema mismatch, redirect inesperado), produce un error estructurado. El Judge decide y el Remapper repara. Sólo entonces vuelve a haber LLM en el path.

## Consequences

### Positivas

- **Costo por scrape ≈ $0 LLM** en happy path. El Validator (DeepSeek texto) cuesta unos centavos; el grueso del costo LLM se paga sólo cuando hay primer mapeo o reparación.
- **Determinismo**: misma entrada → misma salida. El runner se puede testear con replay HAR sin pegarle al banco.
- **Latencia baja**: sin round-trips a LLM por step. Un scrape termina en segundos, no minutos.
- **Auditabilidad**: el `map.json` es texto humano-legible, versionado en git, diffable. Una review de seguridad no requiere leer prompts.
- **Sandbox compatible**: el DSL whitelisted del parser elimina código arbitrario, encaja con la network policy estricta del Docker per-job.
- **Replay**: Playwright HAR record/replay permite reproducir bugs sin acceso al banco real.

### Negativas

- **Complejidad inicial**: hay dos componentes que diseñar (Mapper + Runner) en lugar de uno (LLM in loop). El DSL del parser hay que diseñarlo bien.
- **Repair flow no es gratis**: requiere Judge + Remapper + HITL para los casos dudosos. Más superficie operativa.
- **Map staleness**: si el banco cambia y nadie corre, el primer scrape post-cambio falla. La cadena Judge → Remapper lo resuelve, pero no es instantáneo.
- **Cobertura limitada por el DSL**: si un banco hace algo raro (ej. captcha por imagen, multi-step JS dinámico), el DSL puede no alcanzar y hay que extenderlo (con cuidado para no abrir agujeros de seguridad).

### Operativas

- Necesitamos infraestructura para versionar `map.json` (git), firmar maps oficiales (sigstore), separar `community/` con warning ([ADR-0010](./0010-license-agpl-3.md), [ADR-0014](./0014-browser-use-as-mapper-foundation.md)).
- El Runner consume `map.json` por hash, no sólo por nombre, así un rollback es atómico.

## Alternatives Considered

### Opción B — LLM in loop por scrape

**Rechazada.** Razones concretas:

- **Costo prohibitivo**: $0.10–$1 por job según volumen. Multiplicado por miles de jobs/día en self-hosts activos = costo dominante del producto.
- **No determinístico**: dos corridas idénticas pueden divergir; los bugs son intermitentes y difíciles de reproducir.
- **Latencia inaceptable**: cada step LLM agrega segundos. Un scrape de 6 pasos pasa de ~10s a ~60s+.
- **Ataque de prompt injection**: si el banco renderiza contenido controlado por el cliente (campos de descripción de transacción), un LLM en loop puede ser manipulado. Un runner determinístico no se "convence" de ejecutar nada.
- **Replay imposible**: no se puede reproducir bugs sin reproducir el estado completo del LLM provider.

### Opción C — Mapas escritos a mano

**Rechazada como única estrategia, parcialmente válida.** Razones:

- Para bancos estables, mapas a mano son perfectamente válidos y de hecho los oficiales pueden ser revisados/refinados a mano.
- Pero **no escala**: cada banco PA + cada cambio = trabajo manual. El Mapper LLM elimina la fricción del primer mapeo y baja el costo de reparación.
- Combinable: los `map.json` producidos por el Mapper se commitean a git; el operador puede editarlos a mano si quiere. No son cajas negras.

### Opción D — Hybrid LLM in loop con cache de decisiones

Considerada como variante de B. Rechazada por la misma razón fundamental: en steady state seguís dependiendo del LLM. La cache se invalida con cualquier cambio menor del banco.

## Status: Accepted (2026-05-09)
