# Workflow: doc-keeper

Mantener `docs/`, `docs/adr/` y `DECISIONS.md` sincronizados con el código y las decisiones intermedias usando git como fuente de verdad.

## Componentes

| Pieza | Path | Rol |
|-------|------|-----|
| Subagente | `.claude/agents/doc-keeper.md` | Lee git diff, transcript y aplica updates a docs |
| Slash command | `.claude/commands/sync-docs.md` | Disparo manual: `/sync-docs [rango-git]` |
| Hook Stop | `.claude/hooks/check-doc-drift.sh` | Detecta drift al final de cada turno e inyecta recordatorio |
| Settings | `.claude/settings.json` | Registra el hook Stop y un PostToolUse para ADRs |

## Flujo

```
┌─ Usuario edita código ──────────────┐
│                                     │
│  Edit/Write → git working tree      │
│                                     │
└──────────────┬──────────────────────┘
               │
               ▼
   ┌──────────────────────────────┐
   │ Stop hook: check-doc-drift   │  ← se dispara al final del turno
   │ Compara código vs docs en    │
   │ git status                   │
   └──────────────┬───────────────┘
                  │
        ¿drift?   │
        ──────────┴──────────
        sí                  no
        │                    │
        ▼                    ▼
  Inyecta context       Continúa sin
  "ejecuta /sync-docs"  fricción
        │
        ▼
  Usuario corre /sync-docs
        │
        ▼
  ┌──────────────────────────┐
  │ doc-keeper subagent      │
  │ - git diff/log/status    │
  │ - lee transcript         │
  │ - categoriza cambios     │
  │ - edita docs/ + ADRs     │
  │ - reporta drift restante │
  └──────────────────────────┘
```

## Cuándo se invoca

1. **Automático** — Stop hook al cerrar turno detecta `code changes && !docs changes` → recuerda al usuario.
2. **Manual** — `/sync-docs` o `/sync-docs HEAD~5..HEAD` para rangos arbitrarios.
3. **Proactivo del modelo** — Claude puede invocar al subagente `doc-keeper` cuando detecte una decisión arquitectónica intermedia en la conversación, sin esperar al hook.

## Reglas de oro

- Git es la fuente. Si no está en `git diff` ni en el transcript de la sesión, no se documenta.
- ADRs son inmutables salvo errata. Decisiones reversadas → ADR nuevo con `Supersedes: ADR-NNNN`.
- `DECISIONS.md` siempre refleja el estado actual; los ADRs preservan la historia.
- El agente NO toca código fuente, solo docs.
- Drift detectado se reporta, no se corrige silenciosamente.

## Setup local

1. Copiar archivos:
   ```
   .claude/agents/doc-keeper.md
   .claude/commands/sync-docs.md
   .claude/hooks/check-doc-drift.sh
   .claude/settings.json
   ```
2. `chmod +x .claude/hooks/check-doc-drift.sh`
3. Verificar: `git status && /sync-docs` en una sesión Claude Code.

## Extensiones futuras

- Tag git `last-doc-sync` para que el agente sepa el rango exacto desde la última sincronización.
- CI job que ejecute `/sync-docs HEAD~1..HEAD` en cada PR y comente diff de docs propuesto.
- Métricas: ratio commits-código / commits-docs por sprint para detectar deuda documental.
