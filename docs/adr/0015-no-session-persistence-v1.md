# ADR-0015 — No persistir sesión de browser entre scrape jobs en v1

## Context

Cada scrape job arranca con login completo: ingresar usuario, password, esperar y confirmar OTP/Clave Móvil, llegar a la sección de movimientos. Una optimización tentadora es **persistir cookies / localStorage / sesión del browser entre jobs**, así el siguiente scrape "salta" el login.

En el contexto de bancos panameños, esto choca con realidades operativas:

1. **Timeout de inactividad agresivo**: bancos PA cierran sesión típicamente entre 3 y 10 minutos de inactividad. Banco General específicamente cierra alrededor de los 5 minutos. Si los scrapes no son frecuentes (lo típico: cada hora, cada día), la cookie cacheada está expirada cuando llega el siguiente job.
2. **Detección anti-bot**: reusar la misma session-id muchas veces seguidas desde el mismo IP es exactamente el tipo de patrón que dispara alertas de fraud detection del banco.
3. **Superficie de ataque**: cookies de sesión bancaria al-rest son material sensible. Cada cookie persistida es un secreto adicional que cifrar, rotar, auditar.
4. **OTP requerido por sesión nueva**: incluso si conserváramos la cookie, muchas operaciones (descargar reporte, ver movimientos extendidos) requieren re-validar OTP cuando el banco detecta un patrón inusual. La "ganancia" es marginal.
5. **Complejidad operativa**: serializar y deserializar el state del browser entre containers efímeros es no-trivial.

Por contraste, **dentro del mismo job** sí necesitamos preservar el browser context durante la ventana de OTP (el cliente puede tardar hasta 4 minutos en aprobar el push en su app). Esa preservación la realiza el proceso `BrowserSidecar` dentro del sandbox container, que mantiene la conexión CDP con Chromium independientemente del ciclo de vida del worker Temporal. La activity `OTPSignalAwaitActivity` mantiene el **slot de signal de Temporal** mediante heartbeat, pero no la conexión al browser. Ver [ADR-0019](./0019-browser-sidecar-otp.md).

## Decision

**v1 no persiste cookies, localStorage, sessionStorage, IndexedDB ni ningún state del browser entre scrape jobs.** Cada job arranca con un browser context nuevo en su Docker container efímero, hace login completo, opera, y al terminar el container se destruye.

**Sí preservamos el browser context dentro del mismo job activo**, específicamente durante la ventana de espera del OTP. Esa preservación la realiza el proceso `BrowserSidecar` dentro del sandbox container ([ADR-0019](./0019-browser-sidecar-otp.md)), no la activity de Temporal. La activity conserva únicamente el slot de signal Temporal vía heartbeat ([ADR-0003](./0003-temporal-orchestration.md)). Si el OTP no llega en 4 minutos, abort + retry; jamás "reusar después".

Esta política es revisable en v2 si encontramos un caso de uso real que la justifique (ej. scrapes de muy alta frecuencia donde el ahorro de login compense las contras).

## Consequences

### Positivas

- **Simplicidad**: cada job es self-contained. No hay state cross-job que cifrar, sincronizar, expirar.
- **Privacidad**: no quedan cookies de sesión bancaria persistidas en el host del operador entre jobs. Reduce blast radius si el host se compromete.
- **Detección anti-bot reducida**: cada job luce como un user nuevo iniciando sesión, patrón normal y esperado por el banco.
- **Sandbox limpio**: el container se crea, opera, se destruye. Ningún artefacto cross-job. Encaja con [ADR-0009](./0009-docker-sandbox-per-job.md).
- **Sin caché coherence problems**: no hay que invalidar cookies expiradas, no hay race conditions entre jobs accediendo al mismo state.

### Negativas

- **Login completo en cada scrape**: agrega latencia (~10-30s por login) y requiere acción del usuario (aprobar OTP) en cada corrida.
- **Costo de OTP push**: el cliente recibe un push por scrape. Si la app del banco impone rate-limit a OTP push, una alta frecuencia de scrapes puede fallar. Mitigación: schedule razonable (no más de cada hora típico).
- **No optimizable más adelante sin cambiar la decisión**: cualquier feature que asuma sesión cacheada no puede construirse encima sin revisitar este ADR.

### Operativas

- El Docker per-job se destruye al final del workflow Temporal. No hay volumen montado para state del browser.
- El volumen montado para downloads (Excel) sí persiste por job en disco del operador, con TTL configurable, pero no contiene state del browser.
- Para tests con replay HAR, el HAR se graba dentro del job y se anonimiza antes de chequearse a fixtures.

## Alternatives Considered

### A — Persistir cookies cifradas en el storage central

Considerada y **rechazada**. Razones detalladas arriba: timeout de 5 min hace que la cookie sea inútil al siguiente job en la mayoría de los casos; superficie de ataque sin beneficio; complejidad sin payoff.

### B — Persistir sólo "device fingerprint" (no la sesión, sólo el browser fingerprint para parecer "el mismo dispositivo")

Considerada para v2. Posible beneficio: el banco confía más en un device conocido y reduce frecuencia de OTP. Posibles contras: si el banco bindea fingerprint a session-id, no funciona; si funciona pero el banco lo detecta como anómalo (mismo device "logueando" cada hora 24/7), peor que no usarlo. **Pospuesta**, no rechazada definitivamente.

### C — Sesión long-lived con keep-alive (mantener el browser abierto entre scrapes en background)

**Rechazada para v1** y probablemente para siempre. Razones:

- Requiere un container long-lived por banco/cuenta, contradice el modelo de sandbox efímero.
- Keep-alive synthetic (refrescar páginas cada minuto) es exactamente el patrón que el banco quiere detectar y bloquear.
- Recursos: N cuentas activas = N browsers en memoria 24/7.
- Riesgo de session hijack si el container se compromete.

### D — Reusar sólo el browser binary, no el state (warm process pool)

Considerada como optimización de performance pura. **Pospuesta**: la ganancia (cold start de Chromium) es del orden de segundos, vs decenas de segundos del login. No es prioritario. Reevaluable en v2 si el cold start se vuelve dominante.

## Cross-references

- **ADR-0019** — [BrowserSidecar: proceso dedicado que mantiene la sesión Playwright/CDP durante OTP wait](./0019-browser-sidecar-otp.md): detalla cómo se preserva el browser context *dentro* del job durante la ventana OTP, complementando la política de este ADR (que prohíbe persistencia *entre* jobs).

## Status: Accepted (2026-05-09)
