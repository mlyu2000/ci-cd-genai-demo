"""Cascade fixture switcher: puts the repo into one of the demo failure states
and pushes it to the GitLab remote so a real pipeline hits that exact failure.

States (each a genuine, deterministic CI failure — no fakes):
  S1  pool_exhaustion   pool.py 5/0 vs 6 workers   -> test_integration fails
  S2  missing_retry     client.py WITHOUT retry    -> test_client fails
  S3  missing_import    service.py missing import  -> test_service fails
  final (all fixed)     everything green           -> final Run Pipeline is green

Current repo default = S1 (the classic demo). The cascade engine (app/main.py)
switches state between auto-fix rounds: S1 -> fix -> S2 -> fix -> S3 -> fix ->
final (green). Each switch is a real commit+push on master (pushes never
trigger pipelines — the workflow rule excludes them).

Usage:
    python scripts/cascade_fixture.py S1|S2|S3|final
"""
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
POOL = os.path.join(REPO, "app", "db", "pool.py")
CLIENT = os.path.join(REPO, "app", "client.py")
SERVICE = os.path.join(REPO, "app", "service.py")

POOL_BROKEN = {"POOL_SIZE": "5", "MAX_OVERFLOW": "0"}
POOL_FIXED = {"POOL_SIZE": "10", "MAX_OVERFLOW": "5"}

CLIENT_FIXED = '''"""External HTTP client for the payments service.

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
'''

CLIENT_BROKEN = '''"""External HTTP client for the payments service.

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
'''

SERVICE_FIXED = '''"""User service for the payments app."""
from typing import Dict, Optional


def get_user(id: Optional[int]) -> Dict:
    """Return a user record by id (None for unknown ids)."""
    return {"id": id, "name": "demo-user", "plan": "enterprise"}


def get_user_orders(id: Optional[int]) -> list:
    return []
'''

SERVICE_BROKEN = '''"""User service for the payments app.

(Import of Optional is missing in this fixture state — scenario
`missing_import`; the module raises NameError at import time.)
"""
from typing import Dict


def get_user(id: Optional[int]) -> Dict:
    """Return a user record by id (None for unknown ids)."""
    return {"id": id, "name": "demo-user", "plan": "enterprise"}


def get_user_orders(id: Optional[int]) -> list:
    return []
'''

# (pool, client, service) content per state
STATES = {
    "S1": (POOL_BROKEN, CLIENT_FIXED, SERVICE_FIXED),
    "S2": (POOL_FIXED, CLIENT_BROKEN, SERVICE_FIXED),
    "S3": (POOL_FIXED, CLIENT_FIXED, SERVICE_BROKEN),
    "final": (POOL_FIXED, CLIENT_FIXED, SERVICE_FIXED),
}

LABELS = {
    "S1": "DB pool exhaustion (pool.py 5/0 vs 6 workers)",
    "S2": "external API timeout without retry (client.py)",
    "S3": "missing import NameError (service.py)",
    "final": "all fixtures fixed (pipeline green)",
}


def _git(args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _set_pool(values):
    with open(POOL) as f:
        content = f.read()
    changed = []
    for key, val in values.items():
        if not re.search(rf"^{key}\s*=\s*{val}\b", content, re.M):
            content = re.sub(rf"^{key}\s*=\s*\d+", f"{key} = {val}", content, flags=re.M)
            changed.append(key)
    with open(POOL, "w") as f:
        f.write(content)
    return changed


def _write_if_differs(path, content):
    old = open(path).read()
    if old != content:
        with open(path, "w") as f:
            f.write(content)
        return os.path.relpath(path, REPO)
    return None


def _gitlab_remote():
    remotes = _git(["remote", "-v"]).stdout
    for line in remotes.splitlines():
        name = line.split("\t")[0]
        url = line.split("\t")[1] if "\t" in line else ""
        if "18929" in url or name == "gitlab":
            return name
    return "origin"


def apply(state: str) -> int:
    if state not in STATES:
        print("unknown state:", state, "(use S1|S2|S3|final)")
        return 2
    pool_vals, client_src, service_src = STATES[state]

    # Sync with the gitlab remote first (a prior round may have advanced it).
    remote = _gitlab_remote()
    _git(["fetch", remote, "master"])
    ahead = _git(["rev-list", "--count", "HEAD..%s/master" % remote]).stdout.strip()
    dirty = _git(["status", "--porcelain"]).stdout.strip() != ""
    if dirty:
        s = _git(["stash", "push", "-u", "-m", "cascade_fixture auto-stash"])
        if s.returncode != 0:
            print("could not stash uncommitted changes:", s.stderr)
            return 1
    if ahead.isdigit() and int(ahead) > 0:
        b = _git(["rebase", "%s/master" % remote])
        if b.returncode != 0:
            print("rebase failed:", b.stderr)
            _git(["rebase", "--abort"])
            if dirty:
                _git(["stash", "pop"])
            return 1
        print(f"rebased onto {remote}/master ({ahead} new commit(s))")
    if dirty:
        _git(["stash", "pop"])

    _set_pool(pool_vals)
    c1 = _write_if_differs(CLIENT, client_src)
    c2 = _write_if_differs(SERVICE, service_src)
    if not (c1 or c2 or _git(["status", "--porcelain", "app/db/pool.py"]).stdout.strip()):
        print(f"already in state {state} ({LABELS[state]}); nothing to push")
        return 0

    _git(["add", "app/db/pool.py", "app/client.py", "app/service.py"])
    _git(["commit", "-m", f"chore: cascade fixture -> {state} ({LABELS[state]})"])
    p = _git(["push", remote, "HEAD:refs/heads/master"])
    if p.returncode != 0:
        print("push failed:", p.stderr)
        return 1
    print(f"fixture switched to {state}: {LABELS[state]} (pushed to {remote})")
    return 0


if __name__ == "__main__":
    sys.exit(apply(sys.argv[1] if len(sys.argv) > 1 else "S1"))
