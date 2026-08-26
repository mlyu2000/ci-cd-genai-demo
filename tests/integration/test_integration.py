"""Integration test: payments service under load (the demo's deterministic failure).

Reads the REAL pool configuration from ``app/db/pool.py``. With the current
config (POOL_SIZE=5, MAX_OVERFLOW=0) the pool serves 5 connections but 6
parallel workers need one each -> this test fails, and it is the failure the
GenAI agent triages. Applying the agent's fix (raising the pool) turns it green.

This is a real assertion against real config — not a coin flip, not a mock.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app"))

from db.pool import POOL_SIZE, MAX_OVERFLOW, EXPECTED_WORKERS  # noqa: E402


def test_payments_pool_capacity():
    """Every parallel worker must be able to get a DB connection."""
    effective = POOL_SIZE + MAX_OVERFLOW
    assert effective >= EXPECTED_WORKERS, (
        f"DB pool exhausted: {effective} connections available "
        f"(POOL_SIZE={POOL_SIZE} + MAX_OVERFLOW={MAX_OVERFLOW}) but "
        f"{EXPECTED_WORKERS} parallel workers need one each"
    )
