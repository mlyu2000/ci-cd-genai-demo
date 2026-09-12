"""External HTTP client for the payments service.

The integration test (tests/integration/test_client.py) exercises this against
a simulated endpoint whose FIRST attempt times out (>2s). With the retry
wrapper below, the second attempt succeeds and the job is green. WITHOUT the
retried path (fixture state for scenario `missing_retry`) the timeout
propagates and the integration job fails. The GenAI agent's fix for that
scenario is to add the retry decorator (see scenario `missing_retry`).
"""
import time

import requests
from tenacity import retry, stop_after_attempt, wait_fixed


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def fetch(url: str, timeout: float = 2.0) -> dict:
    """GET a JSON endpoint, retrying transient timeouts (3 attempts, 1s backoff)."""
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
