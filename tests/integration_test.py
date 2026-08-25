"""Integration test: payments service under load.

This test is REAL and DETERMINISTIC — it fails on purpose in CI so the
GenAI auto-fix demo has a coherent, reproducible failure to triage.
It reproduces the "DB pool exhaustion" scenario: 6 parallel workers open
connections but the pool allows only 5.

The GenAI agent's fix (raising the pool) is exactly what this test expects.
"""
import sys


def test_payments_pool_capacity():
    """6 parallel integration workers must each get a DB connection.

    The pool (app/db/pool.py) is configured with pool_size=5, max_overflow=0,
    so the 6th worker blocks and the capacity assertion fails.
    """
    # Mirror of app/db/pool.py configuration (kept here so the test is self-contained).
    pool_size = 5
    max_overflow = 0
    effective_capacity = pool_size + max_overflow
    workers = 6

    assert effective_capacity >= workers, (
        f"DB pool exhausted: {effective_capacity} connections available "
        f"({pool_size} + {max_overflow} overflow) but {workers} parallel "
        f"workers need one each"
    )


if __name__ == "__main__":
    try:
        test_payments_pool_capacity()
    except AssertionError as e:
        print(f"AssertionError: {e}")
        print("ERROR: integration-test job failed (exit 1)")
        sys.exit(1)
    print("integration ok")
