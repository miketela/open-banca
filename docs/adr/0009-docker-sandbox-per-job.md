# ADR-0009 — Container Docker efímero por scrape job

## Contexto

Cada scrape job ejecuta Chromium controlado por Playwright (y, en Mapper/Remapper, browser-use con LLM en el loop). Tres riesgos centrales:

- Chromium es histórico vector de CVE. Un 0-day en V8 podría dar RCE en el proceso.
- Las credenciales bancarias del usuario están descifradas en memoria durante el job.
- El banco mismo puede servir contenido inesperado o user-controlled (campo de beneficiario, mensajes) que abra superficie de prompt injection o de exfiltración.

Necesitamos aislamiento fuerte entre jobs y entre el job y el host, con network policy granular por banco.

## Decisión

Cada scrape job corre en un **container Docker efímero**, spawned por el orchestrator antes del scrape y destruido al finalizar. Características:

- Imagen base hardenizada con Chromium pinned por SHA digest, scaneada con Trivy en CI, firmada con cosign.
- User non-root, read-only rootfs, tmpfs para `/tmp` y `/secrets`.
- Capabilities `--cap-drop=ALL`, sin `cap_add`.
- Seccomp default + denylist extra (`unshare`, `keyctl`, `bpf`).
- AppArmor/SELinux profile dedicado.
- `--no-new-privileges`, `--ipc=none`, `pids-limit=256`, `memory=2GiB`, `cpus=1.5`.
- Network mode user-defined con egress proxy en deny-by-default; allowlist por banco + LLM gateways.
- Resolver DNS local que sólo resuelve hosts en allowlist.
- TTL hard 6 min (banco corta sesión a ~5 min).

Detalle: [`04-security/sandbox.md`](../04-security/sandbox.md).

## Sandbox manager y `/var/run/docker.sock`

El orchestrator necesita spawn/kill de containers. Dos modelos:

- **Docker-in-Docker (dind)**: child Docker daemon dentro del orchestrator container. Pro: no expone socket del host. Con: overhead, complejidad de storage drivers, conocido por bugs sutiles.
- **Sibling containers via socket compartido**: bind mount de `/var/run/docker.sock` del host al container del orchestrator. Pro: simple, performante. Con: el socket es root-equivalent en el host.

**Decisión**: sibling containers + **docker-socket-proxy** intermedio. El orchestrator habla con el proxy, no con el socket directo. El proxy expone sólo los endpoints estrictamente necesarios:

- `POST /containers/create`, `POST /containers/{id}/start`, `POST /containers/{id}/kill`, `DELETE /containers/{id}`, `POST /containers/{id}/wait`, `GET /containers/{id}/json`, `GET /containers/{id}/logs`.
- Deny todo lo demás (especialmente `/volumes/create` con bind del host, `/exec`, `/build`, `/images/create`).

Smoke test obligatorio en CI: el orchestrator intenta endpoints prohibidos contra el proxy y verifica 403.

## Alternativas consideradas

### Subprocess + seccomp + namespaces manual
- **Rechazada**: reinventa pieza por pieza lo que Docker ya da. Más liviano (no hay arranque de container) pero más código de seguridad propio.
- Trade-off perdido: ~2-3s de overhead por job que añade Docker.

### gVisor o Firecracker
- **Rechazada para v1**: aislamiento más fuerte (hypervisor / user-space kernel) pero overkill operativo. gVisor rompe algunas syscalls de Chromium; Firecracker requiere KVM y agrega imagen rootfs custom.
- **Postergada a v2**: si emerge necesidad real (e.g. multi-tenant en host compartido).

### Container persistente con session reset por job
- **Rechazada**: violar la propiedad efímera anula el blast radius y expone state cross-job. Además el ADR-0015 ya decidió no persistir sesión cross-job.

### Sin sandbox (proceso directo)
- **Rechazada**: Chromium con CVE → RCE en el host con la master passphrase del Vault en memoria. Inaceptable.

## Consecuencias

Positivas:

- Blast radius acotado: un escape de Chromium choca contra el namespace + seccomp + AppArmor + caps drop antes de tocar el host.
- Network policy granular por banco; SSRF a metadata IP (169.254.169.254) y RFC1918 bloqueados por defecto.
- Reproducible: la imagen versionada garantiza que el sandbox de hoy es el mismo que el de hace 3 meses.
- Limpieza automática: tmpfs liberado por kernel al destruir; sin garbage cross-job.

Negativas / costos:

- Overhead de arranque ~2-3s por job. Aceptable para jobs que tardan 30s-3min en total.
- Operacionalmente requiere Docker en el host con docker-socket-proxy bien configurado (golden config + smoke test).
- TTL hard 6 min puede cortar jobs lentos; documentado, configurable.
- Storage de imagen (~500 MB con Chromium) por banco si decidimos imágenes específicas.

Detalle operativo: [`04-security/sandbox.md`](../04-security/sandbox.md). Threat model: [`04-security/threat-model.md`](../04-security/threat-model.md) (T07, T14).

## Status

Accepted (2026-05-09)
