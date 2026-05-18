#!/usr/bin/env python3
"""Terminal UX para prompt_user en scrapes headless.

El worker (Playwright headless en servidor) pausa en ``human_input_required``,
guarda la pregunta en DB y hace poll hasta que llegue la respuesta.

Este script es la interfaz del operador:
  1. (Opcional) ``--start-scrape`` → POST /scrape
  2. Poll GET /jobs/{id} hasta ``human_input_required``
  3. Muestra la pregunta en terminal y lee tu respuesta
  4. POST /jobs/{id}/human-input → el worker continúa

Variables de entorno:
  OPEN_BANCA_API_URL      (default http://127.0.0.1:8000)
  OPEN_BANCA_API_TOKEN    o API_KEY — bearer obligatorio
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _base_url() -> str:
    return os.environ.get("OPEN_BANCA_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _token() -> str:
    token = os.environ.get("OPEN_BANCA_API_TOKEN") or os.environ.get("API_KEY")
    if not token:
        print(
            "Falta OPEN_BANCA_API_TOKEN o API_KEY en el entorno.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return token


def _api(method: str, path: str, body: dict | None, token: str) -> tuple[int, object]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if method == "POST" and body is not None:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{_base_url()}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        return exc.code, detail
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        print(
            f"No se pudo conectar a {_base_url()}: {reason}\n"
            "¿Está la API arriba?  uv run uvicorn open_banca_api.main:app --port 8000",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


def _check_api(token: str) -> None:
    code, _ = _api("GET", "/jobs/00000000-0000-0000-0000-000000000000", None, token)
    if code not in (200, 404):
        print(f"API respondió inesperadamente (HTTP {code})", file=sys.stderr)
        raise SystemExit(1)


def _start_scrape(token: str, *, bank_id: str, credentials: str) -> str:
    code, payload = _api(
        "POST",
        "/scrape",
        {"bank_id": bank_id, "credentials": credentials, "mode": "full"},
        token,
    )
    if code not in (200, 202):
        print(f"POST /scrape falló ({code}): {payload}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(payload, dict) or not payload.get("job_id"):
        print(f"Respuesta /scrape sin job_id: {payload!r}", file=sys.stderr)
        raise SystemExit(1)
    return str(payload["job_id"])


def _watch_job(
    job_id: str,
    token: str,
    *,
    poll_interval: float,
    persist: bool,
) -> int:
    print(f"Esperando human_input_required en job {job_id} …")
    print(f"(API {_base_url()} — el scrape corre headless en el worker)\n")

    while True:
        code, payload = _api("GET", f"/jobs/{job_id}", None, token)
        if code == 404:
            print("Job no encontrado", file=sys.stderr)
            return 1
        if code != 200:
            print(f"GET /jobs error {code}: {payload}", file=sys.stderr)
            return 1

        status = payload.get("status", "")
        if status == "human_input_required":
            pending = payload.get("pending_human_input") or {}
            field_key = pending.get("field_key", "security_q_first_job_title")
            question = pending.get("question_text") or "(pregunta no disponible en API)"
            print()
            print("═" * 60)
            print("Pregunta de seguridad")
            print("═" * 60)
            print(question)
            print("═" * 60)
            try:
                answer = input("Tu respuesta: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nCancelado.")
                return 130
            if not answer:
                print("Respuesta vacía, abortando.", file=sys.stderr)
                return 1

            post_code, post_body = _api(
                "POST",
                f"/jobs/{job_id}/human-input",
                {
                    "field_key": field_key,
                    "answer": answer,
                    "persist": persist,
                },
                token,
            )
            if post_code == 204:
                print("Respuesta enviada. El worker headless continúa el map.")
                return 0
            print(f"POST human-input error {post_code}: {post_body}", file=sys.stderr)
            return 1

        if status in ("completed", "failed", "cancelled"):
            print(f"Job terminó con status={status!r} (sin pausa humana).")
            if payload.get("error"):
                print("error:", payload["error"])
            return 2 if status == "failed" else 0

        time.sleep(poll_interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "job_id",
        nargs="?",
        default="",
        help="Job UUID (omitir con --start-scrape)",
    )
    parser.add_argument(
        "--start-scrape",
        action="store_true",
        help="Crear job con POST /scrape antes de esperar la pregunta",
    )
    parser.add_argument("--bank-id", default="banco_general")
    parser.add_argument("--credentials", default="personal")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--persist", action="store_true", default=True)
    parser.add_argument("--no-persist", action="store_false", dest="persist")
    args = parser.parse_args()

    token = _token()
    _check_api(token)

    job_id = args.job_id.strip()
    if args.start_scrape:
        job_id = _start_scrape(token, bank_id=args.bank_id, credentials=args.credentials)
        print(f"Scrape iniciado: {job_id}")
    elif not job_id or not _UUID_RE.match(job_id):
        print(
            "Indica un job_id UUID válido o usa --start-scrape.\n"
            "Ejemplo:\n"
            "  uv run python scripts/answer_human_input.py --start-scrape",
            file=sys.stderr,
        )
        return 1

    return _watch_job(
        job_id,
        token,
        poll_interval=args.poll_interval,
        persist=args.persist,
    )


if __name__ == "__main__":
    raise SystemExit(main())
