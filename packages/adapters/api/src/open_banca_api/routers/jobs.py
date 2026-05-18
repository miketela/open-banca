"""Job management endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer
from open_banca_api.dependencies import (
    TemporalOrchestratorAdapter,
    get_get_job_result_uc,
    get_job_store,
    get_secret_vault,
    get_storage_connection,
    get_temporal_client,
)
from open_banca_api.schemas.jobs import HumanInputRequest
from open_banca_application.use_cases.get_job_result import GetJobResult, GetJobResultInput

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_bearer)],
)


@router.get(
    "/{job_id}",
    summary="Get job status and metadata",
    description="Returns current state and metadata for a job.",
)
async def get_job(
    job_id: str,
    job_store: Annotated[object, Depends(get_job_store)],
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
) -> dict[str, Any]:
    """GET /jobs/{id} — query storage + Temporal for job state."""
    job = job_store.load_job(job_id)  # type: ignore[attr-defined]
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "job_not_found", "job_id": job_id},
        )

    temporal_status = "unknown"
    try:
        temporal_status = await orchestrator.async_query_status(job_id)
    except Exception as exc:
        logger.warning("Could not query Temporal status for job %s: %s", job_id, exc)

    payload: dict[str, Any] = {
        "job_id": job.id,
        "status": str(job.status),
        "bank": job.bank,
        "mode": str(job.mode),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "since_cursor": job.since_cursor,
        "error": job.error,
        "temporal_status": temporal_status,
    }
    from open_banca_domain.entities.job import JobStatus

    if job.status == JobStatus.HUMAN_INPUT_REQUIRED:
        pending = job_store.get_pending_human_input(job_id)  # type: ignore[attr-defined]
        if pending is not None:
            payload["pending_human_input"] = pending
    return payload


@router.get(
    "/{job_id}/result",
    summary="Get job normalized result",
    description="Returns normalized accounts and transactions once job is completed.",
)
async def get_job_result(
    job_id: str,
    uc: Annotated[GetJobResult, Depends(get_get_job_result_uc)],
    job_store: Annotated[object, Depends(get_job_store)],
) -> dict[str, Any]:
    """GET /jobs/{id}/result."""
    try:
        result = uc.execute(GetJobResultInput(job_id=job_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "job_not_found", "message": str(exc)},
        ) from exc

    from open_banca_domain.entities.job import JobStatus

    if result.job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "job_not_completed",
                "status": str(result.job.status),
                "message": "Results are only available once the job status is 'completed'.",
            },
        )

    # Load accounts for this bank alongside transactions
    accounts = job_store.list_accounts_by_bank(result.job.bank)  # type: ignore[attr-defined]

    return {
        "job_id": result.job.id,
        "status": str(result.job.status),
        "accounts": [acc.model_dump() for acc in accounts],
        "transactions": [tx.model_dump() for tx in result.transactions],
    }


@router.post(
    "/{job_id}/otp-confirmed",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm OTP (Clave Móvil push accepted)",
    description="Signals that the client has accepted the Clave Móvil push OTP.",
)
async def otp_confirmed(
    job_id: str,
    job_store: Annotated[object, Depends(get_job_store)],
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
) -> None:
    """POST /jobs/{id}/otp-confirmed — signal Temporal workflow."""
    job = job_store.load_job(job_id)  # type: ignore[attr-defined]
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "job_not_found", "job_id": job_id},
        )

    from open_banca_domain.entities.job import JobStatus

    if job.status != JobStatus.OTP_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "job_not_awaiting_otp",
                "status": str(job.status),
                "message": "Job is not in 'otp_required' state.",
            },
        )

    try:
        await orchestrator.async_signal_otp_confirmed(job_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "signal_failed", "message": str(exc)},
        ) from exc


@router.post(
    "/{job_id}/human-input",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deliver human input for a pending prompt_user step (ADR-0021)",
    description=(
        "Resolves a pending prompt_user step by delivering the answer to the workflow. "
        "Validates field_key regex and answer length/charset (T27a defense). "
        "Optionally persists the answer in the vault for future cache hits."
    ),
)
async def human_input(
    job_id: str,
    body: HumanInputRequest,
    job_store: Annotated[object, Depends(get_job_store)],
    storage_conn: Annotated[object, Depends(get_storage_connection)],
    vault: Annotated[object, Depends(get_secret_vault)],
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
) -> None:
    """POST /jobs/{id}/human-input — deliver answer for prompt_user step.

    Validates:
      - field_key regex ``^[a-z][a-z0-9_]{2,32}$``
      - answer length ≤ 256 bytes UTF-8
      - answer charset: no Unicode control/format characters (T27a)

    Returns 204 on success. Signal is delivered to Temporal workflow.
    """
    job = job_store.load_job(job_id)  # type: ignore[attr-defined]
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "job_not_found", "job_id": job_id},
        )

    from open_banca_domain.entities.job import JobStatus

    if job.status != JobStatus.HUMAN_INPUT_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "job_not_awaiting_human_input",
                "status": str(job.status),
                "message": "Job is not in 'human_input_required' state.",
            },
        )

    job_store.save_human_input_answer(  # type: ignore[attr-defined]
        job_id,
        body.field_key,
        body.answer,
        persist=body.persist,
    )

    if body.persist:
        question_hash = job_store.get_human_input_question_hash(  # type: ignore[attr-defined]
            job_id, body.field_key
        )
        if question_hash is not None:
            credential_id = _resolve_credential_id(storage_conn, job.credential_ref)
            vault.store_security_answer(  # type: ignore[attr-defined]
                credential_id,
                question_hash,
                body.answer,
                field_key=body.field_key,
            )

    try:
        await orchestrator.async_signal_human_input_provided(
            job_id=job_id,
            field_key=body.field_key,
            answer=body.answer,
            persist=body.persist,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "signal_failed", "message": str(exc)},
        ) from exc

    logger.info(
        "human_input delivered: job_id=%s field_key=%s persist=%s",
        job_id,
        body.field_key,
        body.persist,
    )


def _resolve_credential_id(conn: object, credential_ref: str) -> str:
    row = conn.execute(  # type: ignore[attr-defined]
        "SELECT id FROM credentials WHERE label = ? ORDER BY created_at DESC LIMIT 1",
        (f"{credential_ref}:username",),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id FROM credentials WHERE credential_ref = ? LIMIT 1",
            (credential_ref,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "credential_not_found", "credential_ref": credential_ref},
        )
    return str(row[0])


@router.post(
    "/{job_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a job",
    description="Cancels a job in any phase.",
)
async def cancel_job(
    job_id: str,
    job_store: Annotated[object, Depends(get_job_store)],
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
) -> None:
    """POST /jobs/{id}/cancel — cancel a running workflow."""
    job = job_store.load_job(job_id)  # type: ignore[attr-defined]
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "job_not_found", "job_id": job_id},
        )

    try:
        await orchestrator.async_cancel_job(job_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "cancel_failed", "message": str(exc)},
        ) from exc
