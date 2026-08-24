"""Scenario catalog for CI/CD GenAI demo.

Each scenario provides realistic failure context, file contents, and expected fix.
Used by gitlab_mock to seed pipelines and by the UI to show manual baseline.
"""

SCENARIOS = [
    {
        "id": "db_pool_exhaustion",
        "name": "DB Pool Exhaustion in Integration Tests",
        "category": "resource",
        "failing_file": "tests/integration_test.py",
        "changed_file": "app/db/pool.py",
        "trace": """tests/integration_test.py::test_payments_pool FAILED
AssertionError: DB connection pool exhausted (max 5, 6 workers waiting)
  File "tests/integration_test.py", line 42, in test_payments_pool
    assert pool.available() >= workers, 'pool exhausted'
ERROR: integration-test job failed (exit 1)
""",
        "file_before": """# app/db/pool.py
from sqlalchemy import create_engine

engine = create_engine("postgresql://user:pass@localhost/db", pool_size=5, max_overflow=0)
""",
        "file_after": """# app/db/pool.py
from sqlalchemy import create_engine

engine = create_engine("postgresql://user:pass@localhost/db", pool_size=10, max_overflow=5)
""",
        "git_diff": """diff --git a/app/db/pool.py b/app/db/pool.py
@@ -2,4 +2,4 @@ from sqlalchemy import create_engine
 
-engine = create_engine("postgresql://user:pass@localhost/db", pool_size=5, max_overflow=0)
+engine = create_engine("postgresql://user:pass@localhost/db", pool_size=10, max_overflow=5)
""",
        "root_cause": "Connection pool too small for parallel integration workers, causing exhaustion under load.",
        "reasoning_steps": [
            "Integration job runs 6 parallel workers",
            "Pool configured with pool_size=5, max_overflow=0 → max 5 connections",
            "Test asserts pool.available() >= workers → fails when 6th worker waits",
            "Fix is to increase pool_size and allow overflow"
        ],
        "confidence": 0.92,
        "risk_score": 25,
        "validation_commands": ["pytest tests/integration_test.py::test_payments_pool -q", "python app/db/check_pool.py"],
        "manual_triage_minutes": 45,
        "manual_steps": ["Download logs", "Correlate worker count", "Search pool config", "Test locally", "Create PR"],
        "auto_triage_minutes": 2
    },
    {
        "id": "missing_retry",
        "name": "Flaky External API Timeout without Retry",
        "category": "reliability",
        "failing_file": "tests/integration_test.py",
        "changed_file": "app/client.py",
        "trace": """tests/integration_test.py::test_external_api FAILED
requests.exceptions.Timeout: HTTPConnectionPool(host='api.example.com', port=443): Max retries exceeded
  File "app/client.py", line 18, in fetch
    resp = requests.get(url, timeout=2)
ERROR: integration-test job failed (exit 1)
""",
        "file_before": """# app/client.py
import requests

def fetch(url):
    resp = requests.get(url, timeout=2)
    return resp.json()
""",
        "file_after": """# app/client.py
import requests
from tenacity import retry, stop_after_attempt, wait_fixed

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def fetch(url):
    resp = requests.get(url, timeout=2)
    return resp.json()
""",
        "git_diff": """diff --git a/app/client.py b/app/client.py
@@ -1,6 +1,9 @@
 import requests
+from tenacity import retry, stop_after_attempt, wait_fixed
 
+@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
 def fetch(url):
     resp = requests.get(url, timeout=2)
     return resp.json()
""",
        "root_cause": "External API call has no retry logic, causing flaky timeouts under load.",
        "reasoning_steps": [
            "Test intermittently hits API latency >2s",
            "No retry wrapper around requests.get",
            "Add tenacity retry with 3 attempts and 1s backoff"
        ],
        "confidence": 0.85,
        "risk_score": 30,
        "validation_commands": ["pytest tests/integration_test.py::test_external_api -q", "curl -I https://api.example.com"],
        "manual_triage_minutes": 60,
        "manual_steps": ["Reproduce timeout", "Check network traces", "Add retry library", "Update tests"],
        "auto_triage_minutes": 3
    },
    {
        "id": "missing_import",
        "name": "Missing Import Causes Type Error",
        "category": "code",
        "failing_file": "app/service.py",
        "changed_file": "app/service.py",
        "trace": """app.service::test_service FAILED
NameError: name 'Optional' is not defined
  File "app/service.py", line 7, in get_user
    def get_user(id: Optional[int]) -> dict:
ERROR: build job failed (exit 1)
""",
        "file_before": """# app/service.py
from typing import Dict

def get_user(id: Optional[int]) -> Dict:
    ...
""",
        "file_after": """# app/service.py
from typing import Dict, Optional

def get_user(id: Optional[int]) -> Dict:
    ...
""",
        "git_diff": """diff --git a/app/service.py b/app/service.py
@@ -1,5 +1,5 @@
-from typing import Dict
+from typing import Dict, Optional
 
 def get_user(id: Optional[int]) -> Dict:
     ...
""",
        "root_cause": "Optional type used without importing from typing.",
        "reasoning_steps": [
            "Error NameError for Optional",
            "File imports Dict but not Optional",
            "Add import"
        ],
        "confidence": 0.98,
        "risk_score": 10,
        "validation_commands": ["python -m py_compile app/service.py", "pytest app/service_test.py"],
        "manual_triage_minutes": 15,
        "manual_steps": ["Read traceback", "Open file", "Add import", "Commit"],
        "auto_triage_minutes": 1
    }
]

def get_scenario(scenario_id=None):
    import random
    if scenario_id:
        for s in SCENARIOS:
            if s["id"] == scenario_id:
                return s
    return random.choice(SCENARIOS)

def manual_baseline_comparison():
    """Aggregate manual vs auto metrics for demo."""
    total_manual = sum(s["manual_triage_minutes"] for s in SCENARIOS)
    total_auto = sum(s["auto_triage_minutes"] for s in SCENARIOS)
    return {
        "scenarios": len(SCENARIOS),
        "avg_manual_min": round(total_manual/len(SCENARIOS),1),
        "avg_auto_min": round(total_auto/len(SCENARIOS),1),
        "time_saved_pct": round((1 - total_auto/total_manual)*100,1),
        "manual_steps_avg": round(sum(len(s["manual_steps"]) for s in SCENARIOS)/len(SCENARIOS),1)
    }
