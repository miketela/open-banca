#!/usr/bin/env python3
"""Stress test skeleton — 100 concurrent scrape jobs against open-banca API.

Usage:
    export API_KEY=your-token
    export OPEN_BANCA_BASE_URL=http://localhost:8080
    uv run python scripts/stress_test.py

Output:
    docs/05-operations/v1-stress-report.md (generated report)

PRD targets (HU09):
    - p95 cost <= $0.50 per job
    - p95 latency <= 4 min (excluding OTP wait)
    - failure rate < 5%
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_JOB_COUNT = 100
REPORT_PATH = Path(__file__).resolve().parents[1] / "docs/05-operations/v1-stress-report.md"


@dataclass
class JobResult:
    job_id: str
    status_code: int
    latency_s: float
    error: str | None = None


@dataclass
class StressReport:
    started_at: datetime
    finished_at: datetime | None = None
    total_jobs: int = 0
    success_count: int = 0
    failure_count: int = 0
    latencies_s: list[float] = field(default_factory=list)
    results: list[JobResult] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        if self.total_jobs == 0:
            return 0.0
        return self.failure_count / self.total_jobs

    def p95_latency_s(self) -> float | None:
        if not self.latencies_s:
            return None
        sorted_lat = sorted(self.latencies_s)
        idx = int(len(sorted_lat) * 0.95) - 1
        return sorted_lat[max(idx, 0)]


async def submit_scrape(
    client: httpx.AsyncClient,
    *,
    bank_id: str,
    credential_ref: str,
    job_index: int,
) -> JobResult:
    """Submit one POST /scrape and record latency."""
    payload = {
        "bank_id": bank_id,
        "credentials": credential_ref,
        "mode": "incremental",
    }
    headers = {
        "Idempotency-Key": f"stress-{job_index}-{uuid4()}",
    }
    start = time.perf_counter()
    try:
        response = await client.post("/scrape", json=payload, headers=headers)
        latency = time.perf_counter() - start
        return JobResult(
            job_id=response.json().get("job_id", "unknown") if response.is_success else "",
            status_code=response.status_code,
            latency_s=latency,
            error=None if response.is_success else response.text[:200],
        )
    except Exception as exc:
        return JobResult(
            job_id="",
            status_code=0,
            latency_s=time.perf_counter() - start,
            error=str(exc),
        )


async def run_stress_test(
    *,
    base_url: str,
    api_key: str,
    job_count: int,
    bank_id: str,
    credential_ref: str,
    concurrency: int,
) -> StressReport:
    """Run N scrape submissions with bounded concurrency."""
    report = StressReport(started_at=datetime.now(UTC), total_jobs=job_count)
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    ) as client:

        async def _bounded(index: int) -> JobResult:
            async with semaphore:
                return await submit_scrape(
                    client,
                    bank_id=bank_id,
                    credential_ref=credential_ref,
                    job_index=index,
                )

        results = await asyncio.gather(*[_bounded(i) for i in range(job_count)])

    for result in results:
        report.results.append(result)
        if 200 <= result.status_code < 300:
            report.success_count += 1
            report.latencies_s.append(result.latency_s)
        else:
            report.failure_count += 1

    report.finished_at = datetime.now(UTC)
    return report


def write_report(report: StressReport, path: Path) -> None:
    """Write markdown stress report skeleton."""
    p95 = report.p95_latency_s()
    lines = [
        "# v1.0.0 Stress Test Report",
        "",
        f"- **Started:** {report.started_at.isoformat()}",
        f"- **Finished:** {report.finished_at.isoformat() if report.finished_at else 'n/a'}",
        f"- **Total jobs:** {report.total_jobs}",
        f"- **Success:** {report.success_count}",
        f"- **Failures:** {report.failure_count}",
        f"- **Failure rate:** {report.failure_rate:.1%}",
        f"- **p95 latency (submit):** {p95:.2f}s" if p95 else "- **p95 latency:** n/a",
        "",
        "## PRD targets (HU09)",
        "",
        "| Metric | Target | Actual | Pass |",
        "|--------|--------|--------|------|",
        f"| Failure rate | < 5% | {report.failure_rate:.1%} | {'✓' if report.failure_rate < 0.05 else '✗'} |",
        "| p95 cost | <= $0.50 | TBD | TBD |",
        "| p95 latency (excl. OTP) | <= 4 min | TBD | TBD |",
        "",
        "## Notes",
        "",
        "- This skeleton only measures POST /scrape acceptance latency.",
        "- Extend with job polling + cost aggregation before release sign-off.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="open-banca 100-job stress test")
    parser.add_argument("--base-url", default=os.environ.get("OPEN_BANCA_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOB_COUNT)
    parser.add_argument("--bank", default="banco_general")
    parser.add_argument("--credential-ref", default="banco_general")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("API_KEY") or os.environ.get("OPEN_BANCA_API_TOKEN")
    if not api_key:
        raise SystemExit("Set API_KEY or OPEN_BANCA_API_TOKEN")

    report = asyncio.run(
        run_stress_test(
            base_url=args.base_url,
            api_key=api_key,
            job_count=args.jobs,
            bank_id=args.bank,
            credential_ref=args.credential_ref,
            concurrency=args.concurrency,
        )
    )
    write_report(report, args.report)
    print(f"Stress test complete: {report.success_count}/{report.total_jobs} accepted")
    print(f"Report written to {args.report}")


if __name__ == "__main__":
    main()
