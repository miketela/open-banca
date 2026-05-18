#!/usr/bin/env python3
"""Terminal UX for prompt_user: muestra la pregunta y envía la respuesta vía API."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def _api(method: str, path: str, body: dict | None, token: str) -> tuple[int, object]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if method == "POST" and body is not None:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:8000{path}",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", help="Job UUID from POST /scrape")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between GET /jobs/{id} polls",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        default=True,
        help="Persist answer in vault for future cache hits (default: true)",
    )
    parser.add_argument(
        "--no-persist",
        action="store_false",
        dest="persist",
        help="Do not persist answer in vault",
    )
    args = parser.parse_args()

    token = os.environ.get("OPEN_BANCA_API_TOKEN") or os.environ.get("API_KEY")
    if not token:
        print("Falta OPEN_BANCA_API_TOKEN o API_KEY", file=sys.stderr)
        return 1

    job_id = args.job_id
    print(f"Esperando human_input_required en job {job_id} …")

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
            print("Pregunta de seguridad (Banco General)")
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
                    "persist": args.persist,
                },
                token,
            )
            if post_code == 204:
                print("Respuesta enviada. El scrape continúa en el worker.")
                return 0
            print(f"POST human-input error {post_code}: {post_body}", file=sys.stderr)
            return 1

        if status in ("completed", "failed", "cancelled"):
            print(f"Job terminó con status={status!r} (sin pausa humana).")
            if payload.get("error"):
                print("error:", payload["error"])
            return 2 if status == "failed" else 0

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
