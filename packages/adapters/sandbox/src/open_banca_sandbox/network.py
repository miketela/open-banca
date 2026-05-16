"""Per-bank network allowlist for sandbox egress policy (ADR-0009, REQ-009).

Each bank entry lists the exact hostnames the sandbox container is permitted
to reach.  All other outbound traffic is blocked at the Docker network level
(internal=True on the ephemeral per-job bridge + no default gateway).

LLM API endpoints are added at spawn time by the runner and are NOT stored
here — they come from the worker's configuration so that new providers can be
added without touching this file.

Usage:
    from open_banca_sandbox.network import domains_for_bank

    allowed = domains_for_bank("banco_general")
    # -> ["bancogeneral.com", "www.bancogeneral.com"]
"""

from __future__ import annotations

from open_banca_sandbox.exceptions import NetworkPolicyViolation

# ---------------------------------------------------------------------------
# Bank domain registry
# ---------------------------------------------------------------------------
# Keys: canonical bank_id values (snake_case, lowercase).
# Values: non-empty list of FQDNs the sandbox may reach for that bank.
# ---------------------------------------------------------------------------
_BANK_DOMAINS: dict[str, list[str]] = {
    "banco_general": [
        "bancogeneral.com",
        "www.bancogeneral.com",
    ],
    # Future banks:
    # "banistmo": ["banistmo.com", "www.banistmo.com"],
    # "bac": ["bac.net", "www.bac.net"],
}

# LLM provider endpoints always added on top of bank domains.
# These are intentionally separate so they can be overridden via config.
LLM_PROVIDER_DOMAINS: list[str] = [
    "api.anthropic.com",
    "api.deepseek.com",
]


def domains_for_bank(bank_id: str, *, include_llm: bool = True) -> list[str]:
    """Return the full egress allowlist for a bank.

    Args:
        bank_id: Canonical bank identifier (e.g. ``"banco_general"``).
        include_llm: When *True* (default) LLM API domains are appended.

    Returns:
        Deduplicated list of allowed FQDNs.

    Raises:
        NetworkPolicyViolation: If ``bank_id`` is unknown.
    """
    bank_id = bank_id.lower().replace("-", "_")
    if bank_id not in _BANK_DOMAINS:
        known = ", ".join(sorted(_BANK_DOMAINS))
        raise NetworkPolicyViolation(f"Unknown bank_id {bank_id!r}. Known banks: {known}")

    domains = list(_BANK_DOMAINS[bank_id])
    if include_llm:
        for d in LLM_PROVIDER_DOMAINS:
            if d not in domains:
                domains.append(d)
    return domains


def all_known_bank_ids() -> list[str]:
    """Return sorted list of registered bank IDs."""
    return sorted(_BANK_DOMAINS)
