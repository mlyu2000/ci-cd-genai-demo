"""Smoke tests for the CI/CD GenAI demo app (run by GitLab CI unit-test stage)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app"))

os.environ.setdefault("GITLAB_PROJECT_ID", "1")
os.environ.setdefault("GITLAB_MODE", "mock")

# Point the webhook DB at a throwaway file BEFORE importing main (which calls
# webhook.init_db()), so tests never mutate the app's real pipeline_state.db.
import webhook  # noqa: E402
webhook.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="demo_test_"), "pipeline_state.db")

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


def test_merge_refuses_red_or_unknown_pipeline(tmp_path):
    """/api/merge must refuse to merge an MR whose pipeline is not green."""
    import webhook
    webhook.DB_PATH = str(tmp_path / "kpis_test.db")
    webhook.init_db()
    c = m.app.test_client()
    r = c.post("/api/merge", json={"mr_iid": 99, "risk_score": 25})
    body = r.get_json()
    assert body["merged"] is False
    assert "refusing to merge" in body["error"]


def test_fuzzy_apply_rewrites_real_file(tmp_path):
    """_fuzzy_apply applies the semantic +/- change even when git apply would
    reject the hunk (mangled context / index hash — common with small LLMs)."""
    import webhook
    workdir = tmp_path / "wt"
    (workdir / "app" / "db").mkdir(parents=True)
    (workdir / "app" / "db" / "pool.py").write_text(
        'POOL_SIZE = 5\nMAX_OVERFLOW = 0\nEXPECTED_WORKERS = 6\n')
    # exactly the kind of patch qwen3.5-4b emits: real +/- lines, fake index
    patch = (
        "diff --git a/app/db/pool.py b/app/db/pool.py\n"
        "index 1234567..abcdefg 100644\n"
        "--- a/app/db/pool.py\n"
        "+++ b/app/db/pool.py\n"
        "@@ -1,5 +1,5 @@\n"
        '"""wrong context line"""\n'
        "\n"
        "-POOL_SIZE = 5\n"
        "+POOL_SIZE = 6\n"
        " MAX_OVERFLOW = 0\n"
    )
    assert webhook._fuzzy_apply(patch, str(workdir)) is True
    content = (workdir / "app" / "db" / "pool.py").read_text()
    assert "POOL_SIZE = 6" in content
    assert "MAX_OVERFLOW = 0" in content  # untouched lines preserved


def test_fuzzy_apply_noop_when_nothing_matches(tmp_path):
    import webhook
    workdir = tmp_path / "wt"
    workdir.mkdir()
    (workdir / "x.py").write_text("a = 1\n")
    patch = "--- a/x.py\n+++ b/x.py\n@@\n-nonexistent line\n+something\n"
    assert webhook._fuzzy_apply(patch, str(workdir)) is False
    assert (workdir / "x.py").read_text() == "a = 1\n"


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
