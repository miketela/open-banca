# ADR-0014 — Usar la librería `browser-use` como base de Mapper y Remapper

## Context

Mapper y Remapper son agentes LLM con vision que controlan un browser real (Chromium via CDP) para explorar la web del banco y producir/reparar `map.json`. Construir un browser-agent desde cero implica resolver:

- Conexión y manejo del Chrome DevTools Protocol (CDP).
- Loop de agente: tomar screenshot → enviar a LLM → recibir acciones → ejecutar → repetir.
- Event bus interno para coordinar steps async.
- Watchdogs (timeout por step, recovery de pages crasheadas).
- Inyección de "sensitive data" (credenciales) sin que aparezcan en el prompt.
- Output estructurado (Pydantic) del agente.
- Allowed/prohibited domains para evitar navegación fuera del scope.
- Tools custom (descargar archivo, esperar elemento, validar URL).

Existe la librería **`browser-use`** (https://github.com/browser-use/browser-use), MIT-licensed, activamente mantenida (presente en el filesystem del usuario en `/Users/mike/Documents/maybe finance/browser-use`), que ya resuelve todo lo anterior. Es la base de varios agentes browser comerciales y de investigación.

Reescribir esto in-house tomaría semanas y agregaría superficie de mantenimiento permanente. Usar la librería tiene contras: dependencia externa, riesgo de divergir de upstream, tener que entender (y a veces extender) código que no es nuestro.

## Decision

**Mapper y Remapper se construyen sobre la librería `browser-use`** como adapter del `BrowserDriverPort` (en su modo "agent"). Concretamente:

- `adapters/browser/agent/` envuelve `browser-use` y expone la interfaz que necesitan los use cases `RemapBank` y `MapBank` (cuando exista).
- LLM provider se enruta por LiteLLM ([ADR-0005](./0005-pydanticai-litellm.md)) para mantener consistencia.
- El Scraper Runner **no usa `browser-use`**: usa Playwright puro a través de `adapters/browser/runner/`. Los dos viven en el mismo paquete pero no comparten el agent loop.
- Las custom tools que necesitemos (helpers específicos al dominio bancario: descargar Excel, esperar redirect post-login, etc.) se registran como tools `browser-use`.

## Consequences

### Positivas

- **Time-to-market**: Mapper funcional en días, no semanas.
- **CDP, event bus, watchdogs gratis**: nos enfocamos en el dominio bancario, no en infrastructure de browser.
- **Sensitive data injection seguro**: `browser-use` ya tiene patrón para inyectar credenciales sin que aparezcan en el contexto del LLM. Crítico para nuestro modelo de threat.
- **Allowed domains nativo**: combinamos su feature con la network policy del Docker sandbox para defense-in-depth contra navegación fuera de scope.
- **Mantenimiento upstream**: bug fixes, nuevas features (ej. soporte de modelos), mejoras de robustez vienen "gratis" con `pip update`.
- **Comunidad y precedentes**: librería con tracción, mucho material y experiencia compartida.

### Negativas

- **Dependencia externa**: si la librería se discontinúa o cambia de licencia, hay que reaccionar. Mitigación: MIT-licensed (no podemos perder acceso al código actual), está en el filesystem del usuario (podemos vendor/forkear si hace falta).
- **Acoplamiento a su modelo de agente**: si su loop no encaja con un caso nuestro (ej. queremos un patrón de "explore + reflect" muy distinto), tenemos que negociar entre extender la librería vs trabajar con su shape.
- **Versioning discipline**: tenemos que pinear versión exacta y testear upgrades; un breaking change en la librería puede romper Mapper.
- **Superficie de seguridad delegada parcialmente**: si la librería tiene un bug de seguridad (ej. leak de sensitive_data en logs), heredamos el bug. Mitigación: defense-in-depth con network policy, auditar releases mayores.

### Operativas

- Pin de versión exacto en `pyproject.toml`. Renovate / dependabot opcional.
- El adapter `adapters/browser/agent/` aísla la API de `browser-use`. Si en el futuro queremos cambiar de librería, los use cases no se enteran.
- El Scraper Runner es independiente: aunque `browser-use` rompa, los scrapes de bancos ya mapeados siguen funcionando.

## Alternatives Considered

### A — Construir el browser-agent in-house

**Rechazada.** Razones:

- Esfuerzo significativo (semanas) para algo que no es diferenciador del producto.
- Mantenimiento permanente: agent loop, CDP wrapper, recovery, tools — todo sobre nuestros hombros.
- Probabilidad alta de tener bugs sutiles (race conditions en CDP, leaks de sensitive data) que `browser-use` ya resolvió.
- Beneficio marginal: control completo del loop, que sólo nos importa en casos edge.

### B — Skyvern, AgentE, otros agentes browser

Considerados. Algunos son open-source pero menos maduros, menos documentados, o con licencias menos amigables que MIT. `browser-use` ganó por madurez + licencia + presencia en el ecosistema.

### C — Usar Playwright puro también para Mapper (sin agent loop)

**Rechazada.** Sin agent loop con LLM vision no podemos hacer el mapping inicial — esa es exactamente la tarea para la que necesitamos un agente. El Runner es Playwright puro porque ahí no hay decisiones; el Mapper es agente porque ahí sí.

### D — LangChain / CrewAI con tools de browser

Considerada. Rechazada por la misma razón que en [ADR-0005](./0005-pydanticai-litellm.md): API más pesada, menos enfocada en browser específicamente, más fricción para inyección de sensitive data y allowed domains.

### E — Vendor de Selenium o Puppeteer puros + nuestro loop

Considerada. CDP a través de Playwright es preferible: mejor manejo de descargas, mejor stealth-by-default, mejor recording. Y aun así nos tocaría escribir el loop a mano.

## Status: Accepted (2026-05-09)
