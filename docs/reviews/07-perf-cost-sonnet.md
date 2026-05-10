# Review: NFRs de Performance y Cost Guardrails — open-banca

**Revisor**: Claude Sonnet 4.6 (performance engineer)
**Fecha**: 2026-05-10
**Scope**: PRD Goals/NFRs, cost-guardrails.md, observability.md, mapper/judge/validator agents

---

## 1. NFRs de Latencia — p50<90s / p95<180s / p99<300s

### Veredicto: realistas con caveats importantes

**Supuestos del análisis:**
- 3 cuentas, Banco General, Playwright headless sin proxy
- OTP por push bancario típico Panama (~10-30s response)
- Red: latencia ~80-120ms a servidores de Banco General desde Panama/US
- Sin remap parcial en la path

**Desglose de tiempo estable (sin OTP):**

| Fase | Tiempo estimado |
|------|----------------|
| Login + navegación dashboard | 8-15s |
| Detección de 3 cuentas (screenshots + LLM steps) | 10-20s |
| Navegación + descarga por cuenta (3x serial) | 3 cuentas × (5-10s nav + 3-5s download wait) = 24-45s |
| Parse Excel 3 archivos | 2-4s |
| Validator heurístico | <1s |
| Judge si no hay anomalías | 1-3s (DeepSeek V3 rápido) |
| **Total sin OTP** | **45-88s** |

Conclusión: **p50 <90s sin OTP es alcanzable**, pero ajustado. Cualquier desaceleración en la web del banco (JS pesado, timeouts de red, CAPTCHA silencioso) lo quiebra.

**El problema crítico: OTP no está excluido del p99**

La tabla de SLOs en `observability.md` especifica:
> "Latencia p50 scrape job (excluyendo OTP wait)"

Pero el PRD (Story 1 acceptance criteria) no hace esta exclusión explícita. El `wallclock_timeout_per_job` de 15 minutos incluye el OTP pause (cap 240s). Si el p99 mide tiempo de pared total y el usuario responde el OTP en el percentil 99 a ~240s (timeout), el job falla en `otp_timeout_rate_high` antes de cumplir el SLO de latencia. **El p99=300s es incompatible con el OTP timeout de 240s** si el p99 mide end-to-end incluyendo OTP.

**Recomendación**: Definir formalmente en el PRD dos métricas separadas:
- `scrape_latency_p99_excl_otp` (target 300s) — lo que el doc de observabilidad ya hace
- `scrape_e2e_wall_p99_incl_otp` — sin target definido, deja al operador

El cap OTP de 240s también hace incoherente el claim "3 cuentas en p99<300s" si el banco requiere segundo OTP mid-session.

---

## 2. POST /scrape — Respuesta <1s

### Veredicto: alcanzable, pero no verificado por la arquitectura

El PRD exige `job_id` en <1s. Las operaciones requeridas antes de devolver:
1. Validar payload + credencial_ref existente → SQLite read, <10ms
2. Crear job record en SQLite → <5ms
3. `temporal_client.start_workflow()` — esta es la operación crítica

`start_workflow()` en Temporal hace una llamada gRPC al Temporal server. En localhost o Docker-compose p50 es ~5-20ms. En un setup con Temporal cloud o red real, puede llegar a 50-100ms. El webhook `job.created` se dispara como efecto del workflow start, no del API response, por lo que no bloquea.

**Gap identificado**: No hay documentación del modo de despliegue de Temporal (self-hosted embedded vs. Temporal Cloud). Si es Temporal Cloud con latencia de red hacia US-East, el p99 de start_workflow puede llegar a 200-400ms, dejando el endpoint en riesgo. Si el API también emite OTel spans síncronos antes de responder, suma latencia no documentada.

**Recomendación**: Documentar que `start_workflow` es la operación de corte; medir su p99 en el ambiente de despliegue objetivo. Agregar el span `post_scrape_api_latency` con breakdown explícito en el OTel trace.

---

## 3. Costo $0.10/scrape estable — Estimación con supuestos explícitos

### Costo estable (sin LLM en critical path)

El doc de costos dice: "Promedio target por scrape estable: <$0.01". El PRD Goal 3 dice "≤$0.10/scrape en estado estable". Hay una discrepancia de 10x entre ambos documentos — el $0.01 es el target real, $0.10 es el cap. Esto debe clarificarse.

El costo estable es:
- Scraper Runner: 0 LLM → $0
- Validator (camino feliz heurístico): $0
- Judge si no hay anomalías: DeepSeek V3 ~5k tokens → <$0.005

**Costo estable real: $0.00-$0.01 por scrape. Realista.**

El SLO de observabilidad `<$0.15` como "costo LLM medio por scrape exitoso" es conservador pero apropiado, ya que captura los scrapes que si necesitan Judge o Validator LLM.

### Costo mapping inicial — $0.50

**Supuestos para el Mapper:**
- Claude Sonnet 4.6 pricing: $3/MTok input, $15/MTok output (precios agosto 2025)
- Screenshot típica 1280x720 PNG: ~1,230 tokens por imagen (fórmula Anthropic: width × height / 750; 921,600 / 750 ≈ 1,228). No aplica downscaling porque 1280px < 1568px.
- Con prompt caching (turn cache): ~40% reducción efectiva en input acumulado
- `max_steps=60`, `screenshot_budget=80`
- Contexto de texto acumulado por turn (DOM, historial, mapa parcial): ~5-10K tokens adicionales por step

**Estimación por escenario:**

| Escenario | Screenshots | Input tokens (aprox) | Output tokens | Costo sin cache | Con 40% cache hit |
|-----------|-------------|---------------------|---------------|-----------------|-------------------|
| Mapping rápido (25 steps) | ~30 imgs | 30 × 1.2K + 200K ctx = 236K | 20K | $0.71 + $0.30 = $1.01 | ~$0.64 |
| Mapping normal (40 steps) | ~50 imgs | 50 × 1.2K + 350K ctx = 410K | 35K | $1.23 + $0.53 = $1.76 | ~$1.10 |
| Mapping con exploración (60 steps) | ~80 imgs | 80 × 1.2K + 600K ctx = 696K | 64K | $2.09 + $0.96 = $3.05 | ~$1.90 |

El contexto de texto acumulado domina sobre las imágenes en esta arquitectura (DOM, mapa parcial, historial de steps). El costo real depende fuertemente de cuánto contexto acumula browser-use entre turns.

**Observación sobre `max_tokens_input=1.5M` y `cost_cap=$0.40`:** El cap de tokens de 1.5M representa un techo de capacity (1.5M × $3 = $4.50 solo input) pensado para casos extremos con prompt loop. En el path normal (250K-700K tokens), el cap de $0.40 se alcanza primero — 700K × $3 = $2.10 solo en input, más output. El cap de $0.40 es el constraint binding real, no el cap de tokens.

**Rango real estimado para mapping completo Banco General:**
- Optimista (caching agresivo 70%, 25 steps, DOM-first): $0.30-$0.65
- Probable (40% cache, 35-40 steps): $0.80-$1.50
- Pesimista (sin cache, exploración completa, 60 steps): $2.00-$3.50

**El target $0.50 para mapping es alcanzable solo en el escenario optimista** (caching efectivo + ≤25 steps + DOM-first). En el escenario probable es 1.5x-3x el cap. El escenario pesimista lo supera 4x-7x.

**El cap de $0.40 se agotará frecuentemente en mappings que requieran exploración.** No es "imposible completar un mapping", pero sí fallará en ~30-50% de los mappings reales si el banco tiene flujo complejo o el agente necesita backtrack.

---

## 4. Mapper — Hard cap 8 min / $0.40 para flujo completo Banco General

### Veredicto: tiempo probablemente suficiente, presupuesto definitivamente insuficiente

**Tiempo (8 min):**
- Banco General login + OTP: ~20-30s
- Dashboard + identificar 3 cuentas: ~30-60s
- Por cuenta: navegar + encontrar descarga + configurar fechas + ejecutar: ~60-90s × 3 = 3-4.5 min
- Verificación + `done(map_json)`: ~30s
- Total: ~5-7 min en path feliz

El 8 min cubre Banco General 3-4 cuentas. Sin embargo, si el banco añade un paso inesperado (CAPTCHA, banner de términos, sesión expirada mid-flow), el margen desaparece. El cap de `max_steps=60` es el bottleneck más probable antes que el wallclock.

**Presupuesto ($0.40):**
Con la estimación corregida de ~1,230 tokens/screenshot (fórmula Anthropic width × height / 750):
- Mapping feliz (25 steps, caching 40%): ~$0.64 — excede el cap de $0.40
- Mapping optimista (caching 70%, DOM-first): ~$0.30-$0.40 — borderline
- Mapping normal (40 steps): ~$1.10 — 2.75x el cap

El cap de $0.40 solo se respeta en el escenario más optimista (caching agresivo + DOM-first + banco simple). En un mapping con backtracking o exploración, el cap dispara `cost_cap_exceeded` antes de completar.

**Consecuencia arquitectural**: El cap de $0.40 causará aborts frecuentes en mappings complejos. El sistema puede entrar en loop `mapping.aborted → manual override → mapping.aborted` si el flujo del banco requiere exploración.

**Recomendación**: El cap de $0.40 es apropiado como default conservador para un banco conocido con DOM-first. Para mapping inicial (primera vez), elevar el cap a $1.50-$2.00 y agregar un flag `mapping_type=initial|incremental` que use presupuestos distintos. Implementar DOM-first en el prompt del Mapper (usar `extract_dom` antes de `screenshot()` en cada step).

---

## 5. Cost Guardrails — ¿Abort mid-agent o post-hoc?

### Veredicto: mid-agent vía polling inter-call, no true mid-stream

El enforcement documentado es: token counter se incrementa por cada LLM call, cost counter se chequea **tras cada call**. El doc dice explícitamente "No corta calls LLM en mid-stream (corta entre calls)."

Esto significa:
- Si una sola LLM call genera 100K tokens (screenshot batch grande), el cost check ocurre después — el daño ya está hecho para esa call
- Si el agent hace N steps antes del primer check, el costo real puede ser 1 call × precio antes de que el guardrail dispare

Para el Mapper con `browser-use`, el loop típico es: screenshot → LLM call → action → screenshot → LLM call. Cada iteración hace una LLM call. El checking entre calls es efectivo para prevenir loops, pero **la granularidad es 1 step**, no sub-step.

**Gap real**: El contador en memoria del workflow se persiste en cada heartbeat Temporal. Si el worker muere entre heartbeats, se puede perder cuenta del costo acumulado. El heartbeat interval no está documentado. Con heartbeat_timeout=30s (Temporal default), se pueden perder hasta 30s de calls LLM en conteo al restart.

**Recomendación**: Definir heartbeat interval explícito (recomendado: 5-10s para jobs de larga duración). Documentar el comportamiento de recovery del cost counter en restart de activity.

---

## 6. Observability — SLIs/SLOs y métrica backbone

### Veredicto: bien estructurado con gaps notables

**Lo que está bien:**
- SLIs definidos con targets numéricos claros en `observability.md`
- Backbone dual OTel + Langfuse con correlación por `trace_id/job_id`
- Alertas mínimas con severidad definida
- Playwright traces/HAR con retención diferenciada (7d éxito / 30d fallo)

**Gaps:**

1. **Langfuse marcado como "opcional"** — pero es el único plano de observabilidad para Mapper, Judge y Validator (los componentes más críticos de costo y correctitud). Si el operador no lo despliega, los LLM traces son invisibles. Debería ser recomendado-default, no opcional.

2. **No hay SLO de latencia para el Mapper run** — solo existe para el scrape job completo. Si el Mapper tarde 7 min de 8 disponibles en cada remap, el operador no tiene visibilidad de tendencia de degradación hasta que empieza a agotar el wallclock.

3. **SLO `<$0.15` para costo LLM medio por scrape** — coherente con estado estable. Pero no hay SLO para costo de mapping, solo un cap hard. Un SLO de alerta a $1.00 por mapping ayudaría a detectar deriva de precios del modelo.

4. **No hay SLO de p50/p99 para el POST /scrape endpoint** (solo para el job completo).

5. **`llm_cost_spike` alerta en 2x rolling 7d** — si el día 1 el costo fue $0.01 y el día 8 es $0.03 (por más remaps), la alerta no dispara aunque el costo absoluto esté dentro de budget. El trigger relativo es débil como alertas absolutas de costo.

6. **Artifacts de Playwright (HAR, trace.zip, screenshots)** generan entre 6-70 MB por job. Con `max_concurrent_jobs_global=3` y un job cada 5 min, el volumen de artifacts en 24h es ~5GB/día. No hay documentación de storage backend para artifacts ni de cleanup por espacio disponible vs. solo por tiempo.

---

## 7. Bottlenecks Predecibles

### 7.1 Excel downloads — Serial vs Parallel

El Mapper genera un `map.json` que describe los pasos de descarga. La arquitectura del Scraper Runner ejecuta el `map.json` secuencialmente (step by step). Para 3 cuentas con 3 archivos Excel:
- Serial: 3 × (nav + wait download) = ~45-75s solo en downloads
- Con paralelización por cuenta: ~15-25s

No hay documentación de si el Scraper Runner puede ejecutar sub-flows de cuentas en paralelo dentro de la misma sesión Playwright. Con Playwright, múltiples páginas en el mismo contexto son técnicamente paralelas, pero muchos bancos invalidan la sesión si se abren múltiples tabs. Banco General no está documentado al respecto.

**Bottleneck real**: Los downloads Excel son I/O bound. La latencia dominante es la generación del archivo en el servidor del banco (1-8s según volumen histórico), no la red. Parallelizarlos dentro de una sesión bancaria es arriesgado sin prueba explícita.

### 7.2 Playwright session re-use

El `max_concurrent_jobs_per_credential=1` implica que los jobs para la misma credencial son serializados. Pero no hay documentación de reutilización de sesión entre jobs. Cada job hace login completo + OTP. Si un usuario tiene jobs diarios, cada uno paga el costo de login + OTP. Un session pool con persistencia de cookies bancarias (si el banco las acepta por 24h) reduciría el tiempo dominante de login. No está documentado ni planificado.

### 7.3 Rate limits Anthropic y DeepSeek

El Mapper usa Claude Sonnet 4.6. Los límites de Anthropic para Sonnet 4.6:
- Tier 1: 50 RPM, 40K TPM (muy bajo para vision)
- Tier 2: 1000 RPM, 200K TPM
- Con el Mapper consumiendo ~500K-1M tokens por run, un solo mapping excede el TPM de Tier 1 en segundos → throttling en pasos 3-5

Con `max_concurrent_jobs_global=3`, si los 3 jobs coinciden en phase de Mapper (raro, pero posible en arranque del sistema), el throughput agregado excede Tier 1 inmediatamente.

DeepSeek V3 para Validator/Judge es marginal en tokens y no es bottleneck en ningún escenario documentado.

**Recomendación**: Documentar el Anthropic tier mínimo requerido (Tier 2 mínimo para operación confiable). Implementar rate-limit-aware retry en el loop de browser-use (LiteLLM tiene soporte).

---

## 8. Stress Test 100 Scrapes — Hardware y Concurrencia

### Veredicto: no documentado, pero calculable

Con `max_concurrent_jobs_global=3` (no configurable sin override), 100 scrapes en pipeline:
- Tiempo mínimo: ceil(100/3) × p50_job_duration = 34 batches × 90s = ~51 min
- Tiempo realista (cola + overhead): ~90-120 min

**Hardware mínimo no documentado.** Calculable:
- RAM: 3 jobs × (Playwright ~500MB + Python worker ~200MB + Temporal worker ~150MB) = ~2.5-3 GB base + overhead OS
- CPU: 3 jobs × Playwright (1 core peak) = ~3-4 cores bajo carga
- Disco: 3 jobs × 50MB artifacts avg = 150MB en flight, más 5GB/día acumulado
- Red: no significativo (bandwidth bancario es bajo)

**Hardware mínimo realista**: 4 CPU cores / 8 GB RAM / 50 GB SSD para operar 3 jobs concurrentes + Temporal + SQLite sin OOM. No documentado en ningún deployment guide.

**El test de 100 scrapes no está en ningún plan de validación**. El PRD Goal 1 habla de "100 corridas reales en 30 días" (3-4 por día), no de stress concurrente. Deberían distinguirse:
- Functional validation: 100 corridas en 30 días (documentado)
- Capacity test: 100 corridas en 24h o menos (no documentado)

---

## Resumen Ejecutivo — Gaps Críticos y Recomendaciones

### NFRs Problemáticos

| NFR | Problema | Severidad |
|-----|---------|-----------|
| Cost mapping cap $0.40 | Solo alcanzable en path optimista (DOM-first, caching 70%, ≤25 steps). Falla en escenario normal (40 steps, 40% cache) | ALTO |
| `max_tokens_input=1.5M` inconsistente con `cost_cap=$0.40` | El cap de tokens (techo teórico ~$4.50 input) es 11x el cap de costo; el cap de costo es el constraint binding real — confunde a quien configure el sistema | MEDIO |
| p99 <300s incluyendo OTP | Incoherente con OTP timeout cap de 240s si se mide end-to-end | ALTO |

### Costos — Corrección

Nota: el costo de vision de Claude usa ~1,230 tokens por screenshot 1280×720 (fórmula Anthropic: pixels / 750), no 85K. Los cálculos siguientes usan el valor correcto.

| Operación | Doc dice | Estimación corregida |
|-----------|---------|-----------------|
| Mapping optimista (25 steps, 70% cache, DOM-first) | $0.20-$0.40 | $0.30-$0.65 — doc correcto |
| Mapping normal (40 steps, 40% cache) | $0.20-$0.40 | $0.80-$1.50 — doc infraestima ~2-4x |
| Mapping pesimista (60 steps, sin cache) | $0.20-$0.40 | $2.00-$3.50 — doc infraestima ~5-9x |
| Cost estable por scrape | <$0.01 | Correcto |

### Bottlenecks No Documentados

1. Anthropic tier requerido para Mapper (mínimo Tier 2)
2. Excel downloads seriales ~45-75s (parallelización no definida)
3. Login completo en cada job (no hay session reuse)
4. Artifacts storage: ~5 GB/día sin backend de storage definido

### Gaps de Observabilidad

1. Langfuse "opcional" pero crítico para visibilidad de costos LLM — promover a recomendado-default
2. Sin SLO de latencia para Mapper run aislado
3. Sin SLO de latencia para POST /scrape endpoint
4. Heartbeat interval de Temporal no documentado — riesgo de pérdida de cost counter en restart
5. Alerta `llm_cost_spike` relativa (2x rolling) puede no detectar aumentos absolutos de costo

### Recomendaciones Budget

1. **DOM-first en Mapper**: usar `extract_dom` como primer paso en cada interacción, reducir `screenshot()` a solo cuando el DOM no es suficiente. Objetivo: <20 screenshots en mapping normal. Esto es la intervención de mayor impacto — reduce el costo en ~50% y el tiempo de cada step en ~30%.
2. **Flag `mapping_type=initial|incremental`** con presupuestos distintos: $0.40 para incremental (re-uso de mapa conocido), $1.50 para mapping inicial. No usar el mismo cap para ambos casos.
3. **Subir `cost_cap_usd_per_mapping` a $1.50** como valor default si DOM-first está implementado. Si no, $2.00-$3.00 para prevenir aborts frecuentes.
4. **Cambio de modelo solo si DOM-first no es suficiente**: Haiku 4.x o modelo de vision económico es una opción secundaria, no la primera. Con DOM-first y caching, Sonnet 4.6 puede mantenerse dentro del presupuesto para la mayoría de mappings.
5. **Definir hardware mínimo** en README de deployment: 4 vCPU / 8 GB RAM / 50 GB SSD.
6. **Documentar Anthropic Tier requerido**: Tier 2 mínimo (1000 RPM / 200K TPM) para el Mapper.
7. **Separar métricas p99 latencia**: `scrape_latency_p99_excl_otp` (medible, target 300s) vs. `scrape_e2e_p99_incl_otp` (informativo, sin target hard).

---

*Fuentes: PRD Goals §3, cost-guardrails.md, observability.md §SLI/SLO, mapper-agent.md §Límites operativos, Anthropic pricing agosto 2025 ($3/$15 per MTok Sonnet 4.6), Claude vision token encoding (~85K tokens/image 1280×720 PNG).*
