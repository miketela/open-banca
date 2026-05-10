# ADR-0010 — License: AGPL-3.0

## Context

open-banca tiene dos goals declarados en el documento de visión:

1. **Goal técnico**: dar a usuarios de bancos panameños acceso programático a sus propios datos financieros, evitando el lock-in que producen los bancos al no exponer Open Banking.
2. **Goal estratégico explícito**: ejercer **presión institucional** sobre la banca local. Que el proyecto exista — y exista como software libre — funciona como argumento público de que la falta de Open Banking en PA es una decisión política/empresarial, no una limitación técnica. El valor del proyecto crece si su existencia obliga a actores grandes a tomar postura.

Una licencia permisiva (MIT, Apache 2.0) maximiza adopción pero permite que un actor (banco, fintech, vendor) tome el código, le ponga marca propia y lo ofrezca como servicio cerrado. Eso **debilita el goal estratégico**: el costo de fork-and-close es bajo, y la comunidad pierde el upstream.

Una licencia copyleft fuerte (GPL/AGPL) hace que cualquier obra derivada **distribuida** o **operada como servicio** tenga que liberar su código bajo la misma licencia. AGPL extiende GPL al caso "software-as-a-service": si corrés una versión modificada de open-banca como servicio para terceros, tenés que abrir tu fork.

## Decision

Licencia oficial del proyecto: **AGPL-3.0** (`SPDX: AGPL-3.0-only`).

- Todos los archivos del repo (excepto fixtures anonimizadas y datos de terceros con licencia propia) están bajo AGPL-3.0.
- Cada commit incluye DCO sign-off. CLA no requerido v1.
- Maps oficiales firmados (sigstore/cosign) y maps `community/` ambos heredan AGPL-3.0.
- README + LICENSE explícitos: AGPL-3.0 fue elegida intencionalmente; no es la licencia "default", es la licencia "queremos esto".

## Consequences

**Positivas**:

- **Refuerza el goal estratégico**: cualquier actor que quiera operar un fork como SaaS tiene que abrir el código. Encarece el "tomar y cerrar". Mantiene la presión institucional viva.
- **Comunidad con upstream sano**: contribuciones útiles a forks tienden a regresar al core porque el costo de mantener un fork divergente cerrado es alto.
- **Compatibilidad con el ecosistema FOSS**: AGPL es licencia OSI-approved, GNU-recomendada, ampliamente reconocida.
- **Señal clara**: la licencia comunica al ecosistema que el proyecto **no busca** ser absorbido por un vendor cerrado.

**Negativas / costos**:

- **Adopción reducida en empresas con políticas anti-AGPL**: muchas (Google, en algunos contextos) prohíben AGPL internamente. Esto **es deseado** en este proyecto — no queremos servir como subcomponente de un servicio cerrado.
- **Complejidad legal para self-hosters comerciales**: si una fintech corre open-banca para sus propios clientes con modificaciones, debe publicar el fork. Esto puede frenar adopción comercial. **Aceptable**: la audiencia primaria son individuos self-hosting, no empresas.
- **Dual-licensing futuro requiere CLA**: si en algún momento queremos ofrecer una licencia comercial al lado de la AGPL, vamos a necesitar CLA desde el commit 1. v1 no lo requerimos; queda como opción evaluable v2.
- **Dependencias deben ser AGPL-compatible**: Apache 2.0 + AGPL es compatible (AGPL absorbe). MIT + AGPL es compatible. Pero **no podemos depender de bibliotecas con licencias incompatibles** (proprietary, ciertas variantes de SSPL, etc.). Auditoría de licencias en CI.

## Alternatives considered

### Alt 1 — MIT

**Por qué se rechazó**: el goal estratégico requiere disuadir forks cerrados. MIT permite "tomar y cerrar" trivialmente. Para un proyecto con funcionalidad genérica MIT sería razonable; aquí va contra la razón de ser.

### Alt 2 — Apache 2.0

**Por qué se rechazó**: igual que MIT en cuanto a permisividad, con el agregado de patent grant. Útil en ecosistemas corporativos pero irrelevante para nuestro vector. No detiene fork-and-close.

### Alt 3 — GPL-3.0 (sin la "A")

**Por qué se rechazó**: GPL no cubre el caso "operado como servicio sin distribución del binario". Un actor podría correr open-banca como SaaS modificado sin distribuir el código y la licencia no obligaría a abrirlo. AGPL existe específicamente para cerrar ese loophole, y es el caso esperable acá (fintechs operando para terceros).

### Alt 4 — SSPL (Server Side Public License)

**Por qué se rechazó**: SSPL no es OSI-approved (controvertida). Genera fricción legal y de aceptación en la comunidad FOSS. AGPL logra el mismo objetivo con legitimidad establecida.

### Alt 5 — Source-available no-OSI (BSL, Commons Clause)

**Por qué se rechazó**: rompe el goal "open source" del proyecto. La presión institucional pierde fuerza si el proyecto mismo no es libre.

## Status

Accepted (2026-05-09).
