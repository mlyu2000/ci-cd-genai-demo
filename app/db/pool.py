"""Database connection pool configuration for the payments service.

This is the real, load-bearing config that the integration test
(``tests/integration/test_integration.py``) asserts against under parallel load.
The GenAI agent's job in the demo is to raise this so the pool can serve the
configured number of parallel workers.
"""

# Maximum number of persistent connections the pool keeps open.
POOL_SIZE = 6

# Extra connections allowed beyond POOL_SIZE before a worker blocks.
MAX_OVERFLOW = 1

# Number of parallel workers the integration test drives the service with.
EXPECTED_WORKERS = 6


def effective_capacity():
    """Total connections available to parallel workers."""
    return POOL_SIZE + MAX_OVERFLOW
