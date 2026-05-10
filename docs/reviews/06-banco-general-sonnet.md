# Review de viabilidad técnica — Banco General piloto

**Fecha:** 2026-05-10  
**Modelo:** claude-sonnet-4-6  
**Scope:** Evaluación crítica pre-Phase 1 del piloto Banco General para open-banca.

---

## 1. Bloqueantes para Phase 3

Los siguientes ítems son bloqueantes duros: sin resolverlos, Phase 3 (self-healing + Judge) no puede operar correctamente porque sus inputs son inválidos o sus thresholds son arbitrarios.

### BLK-1: Transaction IDs desconocidos → dedup engine en riesgo

La spec marca explícitamente "sin transaction_id estable embebido **(por validar)**". El dedup de 3 niveles del Story 2 (REQ-012) pone el ID embebido como Level 1. Si Banco General no emite IDs en el Excel (como Banistmo, que los esconde en el campo Detail con regexes tipo `-0688-(\d+)`), el engine caerá siempre a Level 2 (fingerprint). Esto es tolerable para full historical pero destruye la precisión del incremental: con la ventana `since - 3 días`, cualquier transacción con descripción idéntica + monto + fecha en esos 3 días de overlap crea falso-positivo de dedup y transacciones se pierden.

El bloqueante no es la ausencia de ID: es que el `parser.json` inicial y el dedup engine se escribirán con un assumption que puede ser incorrecto. Si luego se descubre que hay IDs (quizás en columna "Referencia"), hay que migrar datos ya importados.

**Resolución requerida:** Confirmar con descarga real si existe columna "Referencia" con valor único por transacción, antes de escribir el dedup engine.

### BLK-2: Rango máximo de fechas desconocido → full historical puede estar incompleto o roto

Open question #4 no está resuelta: ¿el banco permite 6 meses, 12, o configurable? El Story 1 AC promete "últimos 6 meses". Si el banco solo permite 90 días (como tarjetas de Banistmo según la memoria), el full historical de savings entregará menos datos de lo prometido sin error visible. Si el portal expone un datepicker libre pero rechaza rangos > N días silenciosamente (devuelve Excel vacío o truncado), el flow no tiene validación para detectarlo.

El `data_end_marker` del parser.json no compara el rango solicitado vs el rango efectivamente devuelto en los metadatos del Excel.

**Nota de clasificación:** Este bloqueante afecta principalmente Phase 1 (resultado incompleto silencioso) y Phase 2 (cursor incremental calculado sobre base truncada). El impacto en Phase 3 es secundario: el Judge nunca recibe BreakageEvent porque el flow "completa" sin error — el problema estructural se oculta. Se mantiene en bloqueantes porque invalida el AC del Story 1 antes de que Phase 3 siquiera entre en juego, y Phase 3 no puede auto-detectar ni corregirlo.

### BLK-3: Comportamiento ante sesión simultánea desconocido — riesgo de loop infinito

Open question #6: si el usuario abre el portal en otro browser mientras el scraper tiene sesión activa, el banco puede expulsar la sesión del scraper. El scraper detectaría esto como un selector_missing o redirect inesperado en medio del flow, no durante el login. El BreakageEvent que emite puede ser ambiguo: Judge podría intentar remap (cost + tiempo) cuando el problema es operacional. No hay step type ni estado para "sesión expirada mid-flow".

Para Phase 3 (auto-remap), esto es especialmente peligroso: el Judge vería una ruptura recurrente, agoraría los 3 remap attempts en 24h, y el banco quedaría en `remap_exhausted` cuando el mapa es correcto.

---

## 2. Spikes recomendados pre-Phase 1

Estos son spikes con creds reales que deben ejecutarse **antes** de escribir código de producción. Cada uno tiene una pregunta binaria a responder.

### SPIKE-1: Estructura real del Excel — ¿hay columna Referencia con ID estable?

**Cómo:** Descargar manualmente 1 Excel de savings y 1 de TC. Revisar si la columna "Referencia" (o equivalente) tiene valores únicos por transacción o valores repetidos/vacíos. Comparar la misma transacción en descargas solapadas de distintas fechas para confirmar estabilidad del valor.

**Decisión que desbloquea:** Si hay ID estable, Level 1 dedup funciona y el incremental es confiable. Si no, hay que diseñar un fingerprint robusto antes de escribir el dedup engine, no después.

**Tiempo estimado:** 2-3 horas.

### SPIKE-2: Formato real del Excel TC vs savings — ¿son estructuralmente distintos?

**Cómo:** Comparar los dos Excels del SPIKE-1. Verificar: misma cantidad de filas de header, mismos nombres de columna, mismo decimal separator, columnas TC-específicas (Pago Mínimo, Saldo a la Fecha de Corte). Confirmar si la metadata de la cuenta (número, tipo, titular) está en celdas absolutas predecibles.

La memoria de Banistmo documenta datos_start_row = 27, con metadatos variables entre fila 1 y 26. Si Banco General tiene estructura similar, el `account_metadata_extraction` del parser.json necesita celdas absolutas correctas o fallará silenciosamente (extrae valores de fila equivocada como account_number).

**Decisión que desbloquea:** Permite escribir el parser.json real y el discriminator de account_type con reglas basadas en columnas reales, no asumidas.

### SPIKE-3: HAR capture del flujo de descarga — ¿hay endpoint JSON detrás del Excel?

**Cómo:** Abrir DevTools Network, hacer login completo y descargar un Excel con HAR capture. Revisar si hay calls XHR/fetch con JSON antes del download, o si el Excel se genera server-side sin exponer datos intermedios.

**Decisión que desbloquea:** Open question #3. Si hay endpoint JSON, la arquitectura de scraping cambia radicalmente (no necesita el Excel). Si no, confirma el path actual.

Como dato de referencia: la memoria de Banistmo documenta que el DOM de la lista de transacciones no tiene IDs y el único lugar con transacciones completas es el Excel. Banco General probablemente siga el mismo patrón (Angular SPA legacy con export server-side), pero hay que confirmarlo.

### SPIKE-4: Comportamiento del push Clave Móvil — ¿web detecta aprobación por polling?

**Cómo:** Durante el spike de login, observar en HAR si hay requests periódicas mientras se espera el push. Verificar si la URL cambia post-aprobación o si es la misma página que muta su estado. Medir el delta de tiempo entre aprobar en app y que la web avance.

**Decisión que desbloquea:** La spec marca "polling/long-poll **(por validar)**". Si es long-poll con timeout de servidor < 4 min, el scraper necesita re-iniciar el poll, no solo esperar. Si es polling con interval de 5s, el browser context necesita mantenerse activo (heartbeat cada 15s ya está en spec, pero el intervalo de polling del banco puede interferir con timeouts del banco).

### SPIKE-5: Detección de cuentas en el menú — ¿los selectores son predecibles?

**Cómo:** Inspeccionar el DOM del dashboard post-login. Verificar si las cuentas tienen selectores con `id` atributos estables (como Banistmo: `#ListItem_depoAcconts_N`, `#ListItem_Credit_N`) o si usan clases Angular generadas.

**Decisión que desbloquea:** Si los selectores son clases Angular (`ng-*`), el Mapper necesita estrategia de texto-matching en lugar de selector directo, lo que reduce confiabilidad del mapeo y aumenta el tiempo del Mapper Agent (más turns de vision para confirmar que seleccionó la cuenta correcta).

---

## 3. Riesgos operacionales

### RIESGO-1: Circuit breaker 2 fallos en 1h — umbral demasiado agresivo para Clave Móvil

El circuit breaker dispara tras 2 logins fallidos consecutivos en 1 hora. El problema: con Clave Móvil, el usuario puede rechazar accidentalmente el push (tap equivocado), generando un "login fallido" real (el banco devuelve a la pantalla de login). Si el operador reintenta inmediatamente, tiene 1 intento restante antes de que el circuit breaker bloquee el job por 1 hora. Dos rechazos accidentales = sistema inoperativo por 1 hora.

Más crítico: el umbral de 2 fallos no distingue entre fallo de credenciales (contraseña incorrecta — merece bloqueo preventivo) y fallo de push (problema de notificación — merece retry con alerting). El banco no distingue entre los dos desde el punto de vista del scraper, pero el riesgo es diferente.

**Riesgo de account lock:** Banco General, como cualquier banco retail, tiene su propio circuit breaker. Si el banco bloquea tras 3-5 intentos fallidos de login en X minutos, los 2 fallos que el sistema permite pueden contribuir directamente al bloqueo del banco. Si la cuenta del operador queda bloqueada, requiere gestión manual con el banco (llamada, sucursal, o unlock por app). La cuenta del operador es la que hace el scraping — es también la cuenta financiera personal. Esto es un riesgo existencial para el piloto.

**Fallback para push perdido: no existe.** Si el push nunca llega (teléfono sin señal, notificaciones silenciadas, app no instalada, push entregado a un device anterior), el spec no tiene fallback. El job falla por `otp_timeout` a los 4 min, y el único camino es `POST /scrape` nuevamente, que genera un nuevo job, una nueva sesión de browser, y un nuevo push. No hay mecanismo para reenviar el push al mismo job ni para cambiar a OTP de código como alternativa. Banco General en su app permite usar código OTP como fallback de Clave Móvil — si el portal web también lo ofrece como alternativa, debería documentarse como paso de fallback en el OTP flow. Si no, el push-never-arrives es un punto de fallo permanente que no tiene resolución automática.

**Recomendación:** El circuit breaker debería: (a) distinguir entre `credential_error` (bloqueo inmediato, alerta urgente) y `push_rejected`/`otp_timeout` (máximo 1 retry antes de pause-for-human), y (b) tener un umbral configurable por banco con default conservador de 1 fallo de credenciales. Para el caso push-never-arrives, documentar en el webhook `job.otp_required` el tiempo estimado de expiración y un link a instrucciones para verificar la app.

### RIESGO-2: T&C de Banco General — scraping probablemente prohibido

La spec no tiene sección de T&C ni menciones legales en banco-general.md. Esto es una omisión notable para un proyecto self-hosted.

Los T&C de banca retail en Panamá típicamente incluyen cláusulas de "uso autorizado" que prohíben acceso automatizado a la plataforma web. Banco General específicamente opera bajo regulación de la Superintendencia de Bancos de Panamá (SBdP), que tiene requirements de seguridad sobre acceso a banca en línea.

El riesgo no es legal para el usuario final (uso personal, self-hosted) en sentido estricto — scraping de datos propios para uso personal es difícilmente perseguible. El riesgo real es:

1. **Cuenta suspendida por seguridad**: Banco General puede detectar patrones de acceso automatizado (user-agent, timing, frecuencia de requests) y suspender la cuenta por "actividad sospechosa" sin aviso.
2. **Responsabilidad del operador**: Si el operator self-hosts para terceros (familia, amigos), pasa de "uso personal" a "proveedor de servicio" con las credenciales de esas personas, lo que sí tiene implicaciones.
3. **Playwright sin headless**: La spec menciona "usar Playwright con UA real, no headless visible al banco" como mitigación de fingerprint. Esto es insuficiente — los bancos modernos detectan Playwright por propiedades del objeto `window.navigator` y `chrome.*`, no solo por headless flag.

**Recomendación pre-Phase 1:** Revisar T&C de Banco General online, agregar sección "Legal Disclaimer" en la documentación del piloto, y documentar que el proyecto es self-hosted para uso personal de las propias credenciales del operador.

### RIESGO-3: Modal de promociones y sesión de 5 min — fragilidad del happy path

La sesión expira en ~5 min y hay un posible modal de promociones al login. Si el Mapper tarda en su primer mapping (8-15 min estimados) y necesita múltiples logins de exploración, puede agotar intentos reales. El Mapper Agent debería tener creds para hacer login real, lo que significa múltiples logins reales durante la fase de mapping — y cada uno activa el push de Clave Móvil.

Esto hace que el Mapper Agent sea impracticable en su forma actual para Banco General: requeriría que el operador esté disponible para aprobar push notifications cada 5 minutos durante 8-15 minutos de mapping. La spec no documenta cómo maneja el Mapper este requerimiento de Clave Móvil durante la fase de exploración.

---

## 4. Gaps en specs

### GAP-1: Mapper Agent + Clave Móvil — flujo no documentado

El Mapper Agent docs no menciona cómo maneja bancos con MFA push durante la fase de exploración. El OTP pause/resume flow está documentado solo para el ScrapeJobWorkflow. Si el Mapper necesita logins múltiples para explorar el portal, ¿emite señales OTP también? ¿El operador debe aprobar cada push? No hay spec para esto.

### GAP-2: Excel TC — campos de statement en parser.json

Story 4 AC requiere que el payload TC incluya: `credit_limit`, `available_credit`, `cut_date`, `min_payment`, `payment_due_date`, `statement_balance`. Estos campos generalmente no están en el Excel de movimientos — están en el header del estado de cuenta o en una pantalla del portal. La spec de parser.json solo cubre celdas del Excel. No hay step type en el Scraper Runner para extraer datos de la pantalla del portal (como el summary de TC en Banistmo, que requiere scraper del DOM).

La memoria de Banistmo documenta este problema: "Scrapar además del Excel: available_credit, total_balance, credit_limit, payment_deadline, interest_rate. Viven en bank_accounts como columnas adicionales." Banco General seguramente sigue el mismo patrón.

El framing correcto aquí es un **gap en el DSL del Scraper Runner**, no solo en el Mapper. El Mapper tiene `extract_dom` para exploración durante mapping, pero el Scraper Runner (Playwright puro) no tiene un step type para extraer texto de la pantalla del portal durante la ejecución productiva. El map.json que genera el Mapper no puede codificar "leer el campo credit_limit de la pantalla antes de descargar el Excel" porque ese step type no existe en el runner. Si se quiere resolver declarativamente, hay que agregar un step type `extract_page_data` al runner DSL. Alternativamente, los campos statement podrían estar en el Excel de TC (en las filas de header/metadata), en cuyo caso el `account_metadata_extraction` del parser.json los cubre — pero esto requiere confirmación en SPIKE-2.

### GAP-3: Decimal separator — asunción sin base

La spec asume punto como decimal separator "típicamente formato US" pero marca "(por validar)". Panamá usa USD y PAB como monedas, pero el software bancario local puede usar coma como separador europeo en exports Excel (es un parámetro del locale del servidor). Si el decimal separator es incorrecto, el `normalize_amount` del parser produce montos incorrectos sin error visible (1.234,56 parseado con locale incorrecto → 1.23456 o error de parse silencioso según la implementación).

### GAP-4: Empty account range — comportamiento no especificado

Open question #8: ¿qué devuelve el banco si no hay movimientos en el rango? Si el banco devuelve un Excel con solo el header (sin filas de datos), el parser.json necesita `data_end_marker: last_row` y debe producir `transactions: []` sin error. Si el banco devuelve un error HTTP o un modal de "no hay datos", el Scraper Runner necesita un step type de assertion o el flow rompe con BreakageEvent. Esto afecta cuentas nuevas o cuentas con actividad esporádica.

### GAP-5: Rate limit — "no agresivo" sin evidencia

La asunción de que el banco no tiene rate limit agresivo tiene cero evidencia base. La open question #10 (¿límite de descargas por día/sesión?) no está resuelta. En el full historical con 3+ cuentas, el sistema hace 3+ descargas en una sola sesión. Bancos panameños típicamente no tienen rate limiting en descarga de Excel (son descargas ocasionales de usuario), pero si existe un límite de, por ejemplo, 5 descargas por sesión y el usuario tiene 6 cuentas, la Story 4 falla para ese usuario sin un mecanismo de manejo.

---

## 5. Path realista a 95% success rate

El 95% success rate fallaría primero en estos puntos, en orden de probabilidad:

1. **OTP timing (push tardío):** El mayor riesgo de failure en producción. Si el usuario recibe el push con delay por conectividad móvil, lo aprueba a los 3:55, y hay 5-10 segundos de processing antes de que el `otp_confirmed` signal llegue al workflow y el browser context verifique el dashboard, la sesión puede estar en estado indeterminado. El canary check puede fallar no porque la sesión murió sino porque el dashboard todavía está cargando post-auth. El spec dice "canary selector falla → `failed_session_lost`" lo cual es incorrecto en este caso — la sesión está viva pero lenta. Necesita un retry breve del canary (3 intentos con 2s de backoff) antes de declarar session_lost.

2. **Fingerprint dedup con duplicate descriptions:** Sin ID estable (pending SPIKE-1), el incremental con ventana de 3 días de overlap va a generar falsos positivos para pagos recurrentes (e.g., "NETFLIX", "ELECTRICIDAD", misma fecha y monto del mes anterior). Con 3 cuentas y scrape mensual, la probabilidad de al menos 1 dedup incorrecto por run es alta.

3. **Modal de promociones no manejado:** Si el banco muestra un modal post-login (probado en otros bancos panameños), y el map.json no tiene step para descartarlo, el siguiente step (navigate to account) fallará. El Judge verá `selector_missing` y puede intentar remap cuando el fix es trivial (dismiss_modal).

4. **Excel TC con formato diferente al esperado:** Si las tarjetas de crédito usan columnas distintas y el `account_type_inference` no detecta correctamente el tipo, el parser aplicará el column_map incorrecto, producirá montos erróneos o campos vacíos, y el validator de post-parse detectará la anomalía (delta balance). Esto genera `job.human_required` en lugar de un resultado, afectando directamente el success rate.

---

## Resumen ejecutivo

**El piloto es técnicamente viable** con las spikes correctas como prerequisito. Los 5 spikes (SPIKE-1 a SPIKE-5) pueden ejecutarse en 1-2 días de trabajo con acceso a credenciales reales y deben realizarse antes de escribir código de producción.

**Los 3 bloqueantes para Phase 3** (BLK-1 IDs, BLK-2 rango fechas, BLK-3 sesión simultánea) no bloquean Phase 1, pero deben estar resueltos antes de que el Judge Agent empiece a tomar decisiones sobre BreakageEvents o el dedup engine procese incrementales.

**El riesgo más inmediato** no es técnico sino operacional: el circuit breaker de 2 fallos puede en combinación con el circuit breaker del propio banco resultar en una cuenta bloqueada. Recomiendo bajar el umbral a 1 fallo de tipo `credential_error` antes del primer test real en producción.

**El gap más significativo en specs** es la ausencia de un flow documentado para Mapper + Clave Móvil, y la falta de step type para extraer datos de pantalla del portal (necesario para los campos statement de tarjetas de crédito del Story 4).
