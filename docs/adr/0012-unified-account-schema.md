# ADR-0012 — Schema canónico unificado con discriminator `account_type`

## Context

open-banca expone tres tipos de cuenta en v1: `savings`, `checking`, `credit_card`. Préstamos quedan para v2.

Cada tipo tiene **campos comunes** (id, banco, moneda, balance, transacciones) y **campos específicos**:

- `credit_card` agrega: `credit_limit`, `available_credit`, `cut_date` (fecha de corte), `payment_due_date` (fecha límite de pago), `min_payment` (pago mínimo), `statement_balance` (saldo a la fecha de corte).
- `savings` y `checking` son casi idénticos en superficie; difieren en metadata operativa (tasa de interés en savings, posiblemente sobregiro en checking).

La pregunta de diseño es: **¿endpoints separados (`/savings-accounts`, `/credit-cards`) con schemas distintos, o un único endpoint `/accounts` con schema discriminado?**

## Decision

Adoptamos un **schema canónico unificado polimórfico con discriminator `account_type`**.

Forma conceptual:

- Campos comunes: `id`, `bank_id`, `account_type` (literal: `savings | checking | credit_card`), `currency`, `balance`, `last_updated`, `transactions[]`, `metadata` (objeto con campos específicos del tipo).
- `metadata` es un payload tipo-específico:
  - `savings`: `interest_rate`, `account_number_masked`.
  - `checking`: `overdraft_limit`, `account_number_masked`.
  - `credit_card`: `credit_limit`, `available_credit`, `cut_date`, `payment_due_date`, `min_payment`, `statement_balance`, `card_number_masked`.

Endpoint único `GET /accounts`, filtrable por `?account_type=`. Un solo modelo Pydantic con `discriminator='account_type'` que valida polimórficamente.

## Consequences

**Positivas**:

- **Cliente más simple**: un solo modelo, un solo endpoint, un solo handler. El consumidor decide qué tipos quiere ver con un filtro.
- **Alineado con arquitectura hexagonal**: el dominio tiene una entidad `Account` con polimorfismo; los adapters no fuerzan a romperla.
- **Evolución natural a más tipos**: agregar `loan` en v2 es agregar un literal al discriminator + un payload nuevo. No requiere endpoint ni schema nuevo, no rompe clientes existentes que filtren por `account_type`.
- **DRY en docs**: una sola sección de schema en OpenAPI.
- **Webhooks consistentes**: `job.completed` lleva `accounts[]` heterogéneo bajo el mismo modelo.
- **Dedup cross-account simplificada**: los matchers fuzzy de transferencias (level 3) operan sobre todas las cuentas con la misma forma base, sin branching.

**Negativas / costos**:

- **Type narrowing en el cliente**: clientes TypeScript/Python deben hacer narrowing por discriminator para acceder a `metadata` específica. Mitigado: Pydantic + OpenAPI generan modelos discriminados nativos, y los SDKs modernos manejan tagged unions trivialmente.
- **Validación más compleja en el servidor**: una operación no soportada para un tipo (ej. `min_payment` en `savings`) tiene que ser rechazada en validation layer. Mitigado por Pydantic discriminator con per-type validators.
- **Riesgo de "metadata catch-all"**: tentación de meter en `metadata` cosas que deberían ser top-level. Disciplina: `metadata` sólo para campos *específicos del tipo*; lo común va al top-level.

## Alternatives considered

### Alt 1 — Endpoints separados por tipo

`GET /savings-accounts`, `GET /checking-accounts`, `GET /credit-cards`, cada uno con su schema.

**Por qué se rechazó**:

- Rompe DRY: tres endpoints, tres schemas, tres handlers, tres secciones de docs.
- Cliente que quiere "todas mis cuentas" hace 3 calls.
- Webhook `job.completed` tendría que llevar 3 listas separadas o tener 3 eventos distintos (`job.completed.savings`, etc.).
- Agregar `loan` en v2 = endpoint nuevo + cliente nuevo + tests nuevos.
- Empuja la complejidad de polimorfismo al cliente (que igual tiene que correlacionar las 3 listas).

### Alt 2 — Schema completamente flat con campos opcionales

Un solo modelo donde `credit_limit?` y `min_payment?` son opcionales en todas las cuentas. Sin discriminator.

**Por qué se rechazó**:

- Pierde garantía de type safety: `credit_limit` ausente en una `savings` es válido pero `null` en una `credit_card` es bug.
- OpenAPI más débil — no expresa la regla "si tipo X entonces estos campos son obligatorios".
- Cliente debe hacer chequeos null-safety por todos lados sin guía del schema.
- Pydantic puede expresarlo, pero es peor DX.

### Alt 3 — `metadata: dict[str, Any]` libre (sin schema interno)

`metadata` como bag de propiedades arbitrarias, validada sólo por convención.

**Por qué se rechazó**: pierde validación, contrato de API frágil, cliente debe parsear lo que sea. Decimos no.

### Alt 4 — GraphQL en vez de REST con interfaces y unions

GraphQL maneja polimorfismo natural con `interface Account` y types `SavingsAccount`, `CreditCard` implementándola.

**Por qué se rechazó (para v1)**: GraphQL agrega peso operacional (server, dataloader, schema design) que no se justifica para un set chico de endpoints. La decisión de stack es FastAPI/REST. v2 podríamos exponer GraphQL al lado, pero la decisión de schema sigue siendo "polimórfico discriminado" — sólo cambia el wire format.

## Status

Accepted (2026-05-09).
