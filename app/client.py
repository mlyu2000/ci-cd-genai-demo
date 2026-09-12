"""External HTTP client for the payments service.

The integration test (tests/integration/test_client.py) exercises this against
a simulated endpoint whose first attempt times out (>2s). Without a retry
wrapper the timeout propagates and the job fails (fixture state for scenario
`missing_retry`). The GenAI agent's fix is to add a retry decorator.
"""
import time

import requests


def fetch(url: str, timeout: float = 2.0) -> dict:
    """GET a JSON endpoint. No retry yet (that is the bug the agent fixes)."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def flaky_payload(attempts: int = 1, slow_after: int = 0) -> dict:
    """Simulated external API response for local demos (not used by CI)."""
    delay = 0.0
    if attempts > slow_after:
        delay = 1.2 + 0.6 * min(1.0, (attempts - slow_after))
    time.sleep(delay)
    return {"ok": True, "attempts": attempts}
