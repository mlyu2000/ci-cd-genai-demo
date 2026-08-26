"""Smoke tests for the CI/CD GenAI demo app (run by GitLab CI unit-test stage)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

os.environ.setdefault("GITLAB_PROJECT_ID", "1")

from flask import Flask  # noqa: E402
import main as m  # noqa: E402


def test_app_imports_and_index():
    assert isinstance(m.app, Flask)
    c = m.app.test_client()
    assert c.get("/").status_code == 200


def test_git_api_returns_info():
    c = m.app.test_client()
    r = c.get("/api/git")
    assert r.status_code == 200
    body = r.get_json()
    assert "commit_hash" in body


def test_analyze_falls_back_gracefully_without_llm():
    c = m.app.test_client()
    r = c.post("/api/analyze", json={"trace": "boom", "job": "integration-test"})
    assert r.status_code == 200
    body = r.get_json()
    # even without an LLM endpoint we get a structured dict
    assert set(["root_cause", "confidence", "risk_score", "patch", "summary"]) <= set(body)


def test_poll_returns_shape():
    c = m.app.test_client()
    r = c.get("/api/poll")
    assert r.status_code == 200
    body = r.get_json()
    assert "pipeline" in body and "jobs" in body


def test_healthz_ok():
    c = m.app.test_client()
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_served_js_is_syntactically_valid():
    """Regression: the HTML is a Python triple-quoted string; a single-backslash
    escape in the embedded JS (\\n, \\b, ...) would be collapsed by Python and
    corrupt the script the browser receives. Extract the served script exactly
    as the app sees it and syntax-check it with node (if available)."""
    import re
    script = re.search(r"<script>(.*)</script>", m.HTML, re.S).group(1)
    # no raw control characters from collapsed escapes
    assert "\x08" not in script, "backspace char leaked into JS (collapsed \\b)"
    try:
        import subprocess
        import shutil
        node = shutil.which("node")
        if not node:
            return  # node not available in this env
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, timeout=30)
            assert r.returncode == 0, f"node --check failed: {r.stderr.decode()[:400]}"
        finally:
            os.unlink(path)
    except FileNotFoundError:
        return
