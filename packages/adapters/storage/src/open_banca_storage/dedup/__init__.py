"""3-level deduplication engine for open-banca transactions.

Level 1 (embedded): bank-supplied stable ID — strongest signal.
Level 2 (fingerprint): deterministic SHA-256 hash of canonical fields.
Level 3 (fuzzy transfer): cross-account transfer matching by amount + date window.
"""

from open_banca_storage.dedup.engine import DedupEngine, IngestResult
from open_banca_storage.dedup.fingerprint import compute_fingerprint
from open_banca_storage.dedup.transfer_matcher import TransferMatcher

__all__ = [
    "DedupEngine",
    "IngestResult",
    "TransferMatcher",
    "compute_fingerprint",
]
