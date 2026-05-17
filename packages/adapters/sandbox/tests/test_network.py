"""Unit tests for open_banca_sandbox.network — bank domain allowlist."""

from __future__ import annotations

import pytest

from open_banca_sandbox.exceptions import NetworkPolicyViolation
from open_banca_sandbox.network import LLM_PROVIDER_DOMAINS, all_known_bank_ids, domains_for_bank


def test_banco_general_domains_present() -> None:
    """banco_general must have both apex and www domains."""
    domains = domains_for_bank("banco_general", include_llm=False)
    assert "bancogeneral.com" in domains
    assert "www.bancogeneral.com" in domains


def test_include_llm_adds_provider_domains() -> None:
    """include_llm=True must append LLM provider endpoints."""
    domains = domains_for_bank("banco_general", include_llm=True)
    for llm_domain in LLM_PROVIDER_DOMAINS:
        assert llm_domain in domains, f"LLM domain {llm_domain!r} missing from allowlist"


def test_exclude_llm_omits_provider_domains() -> None:
    """include_llm=False must not include LLM provider endpoints."""
    domains = domains_for_bank("banco_general", include_llm=False)
    for llm_domain in LLM_PROVIDER_DOMAINS:
        assert llm_domain not in domains, f"LLM domain {llm_domain!r} unexpectedly present"


def test_unknown_bank_raises_network_policy_violation() -> None:
    """Unknown bank_id must raise NetworkPolicyViolation."""
    with pytest.raises(NetworkPolicyViolation, match="Unknown bank_id"):
        domains_for_bank("evil_bank")


def test_bank_id_normalization() -> None:
    """Hyphen-separated and uppercase bank_ids are normalised."""
    # banco-general (hyphen) → banco_general
    domains = domains_for_bank("banco-general")
    assert "bancogeneral.com" in domains

    # BANCO_GENERAL (uppercase) → banco_general
    domains_upper = domains_for_bank("BANCO_GENERAL")
    assert "bancogeneral.com" in domains_upper


def test_no_duplicate_domains() -> None:
    """Each domain must appear exactly once in the returned list."""
    domains = domains_for_bank("banco_general", include_llm=True)
    assert len(domains) == len(set(domains)), "Duplicate domains in allowlist"


def test_all_known_bank_ids_returns_sorted() -> None:
    """all_known_bank_ids() must return a sorted list."""
    ids = all_known_bank_ids()
    assert ids == sorted(ids)
    assert "banco_general" in ids
