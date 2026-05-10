---
description: Lanza al agente doc-keeper para sincronizar documentación con git diff actual
argument-hint: "[rango-git opcional, ej: HEAD~5..HEAD]"
---

Invoca al subagente `doc-keeper` con la siguiente tarea:

Sincroniza la documentación con el estado actual del repositorio.

**Rango git a inspeccionar:** $ARGUMENTS (si vacío: usa `git status` + último commit hasta HEAD)

**Pasos obligatorios:**

1. Ejecuta `git status` y `git diff HEAD` para ver cambios sin commit.
2. Ejecuta `git log --oneline -10` para contexto reciente.
3. Si `$ARGUMENTS` está definido, también `git diff $ARGUMENTS`.
4. Revisa el transcript de esta conversación buscando decisiones intermedias del usuario.
5. Categoriza cambios y aplica updates mínimos a `docs/`, `docs/adr/`, `DECISIONS.md`.
6. Crea ADRs nuevos si detectas decisiones arquitectónicas no documentadas.
7. Reporta archivos tocados, ADRs creados, drift detectado y decisiones que requieran input humano.

NO toques código fuente. Solo documentación.
