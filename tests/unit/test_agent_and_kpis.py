"""Unit tests: GenAI agent helpers + KPI accounting (the honesty layer).

Run: pytest tests/unit -q
These run in the CI unit-test stage and locally via `make test`.
"""
import os
import sys
import sqlite3
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import genai_agent  # noqa: E402


# --------------------------------------------------------------------------
# Agent: trace cleaning keeps the error, drops pip/docker boilerplate
# --------------------------------------------------------------------------
def test_clean_trace_drops_noise_keeps_error():
    trace = (
        "Running with gitlab-runner 19.2.2 (abc)\n"
        "$ pip install pytest\n"
        "Collecting pytest\n"
        "Downloading pytest-8.3.whl (350 kB)\n"
        "Requirement already satisfied flask==3.0.3\n"
        "Installing collected packages: pytest\n"
        "Successfully installed pytest-8.3\n"
        "section_start:1700000000:step_script\n"
        "2026-08-25T14:43:47.215144Z 01O E       AssertionError: DB pool exhausted: 5 connections available\n"
        "2026-08-25T14:43:47.942861Z 00O ERROR: Job failed: exit code 1\n"
    )
    out = genai_agent._clean_trace(trace)
    assert "AssertionError: DB pool exhausted" in out
    assert "ERROR: Job failed" in out
    assert "Collecting pytest" not in out
    assert "Downloading" not in out
    assert "Running with gitlab-runner" not in out


def test_clean_trace_keeps_command_lines():
    trace = "2026-08-25T14:43:47.215136Z 01O $ python -m pytest -q tests/integration\nFAILED tests/integration.py::t\n"
    out = genai_agent._clean_trace(trace)
    assert "$ python -m pytest" in out  # the command echo must survive
    assert "FAILED" in out


def test_clean_trace_keeps_tail_when_long():
    trace = ("noise-line " * 2000) + "AssertionError: the-final-error\n"
    out = genai_agent._clean_trace(trace, limit=200)
    assert out.endswith("AssertionError: the-final-error")
    assert len(out) <= 200


def test_clean_trace_handles_empty():
    assert genai_agent._clean_trace("") == ""
    assert genai_agent._clean_trace(None) == ""


# --------------------------------------------------------------------------
# Agent: retry on 429/5xx, no retry on 4xx
# --------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"choices": [{"message": {"content": "{}"}}]}
        self.text = str(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"{self.status_code} error")

    def json(self):
        return self._body


def test_llm_retries_on_429_then_succeeds(monkeypatch):
    import genai_agent
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp(429) if calls["n"] < 3 else _FakeResp(200)
    monkeypatch.setattr(genai_agent.requests, "post", fake_post)
    monkeypatch.setattr(genai_agent.time, "sleep", lambda s: None)
    genai_agent._call_llm("x")
    assert calls["n"] == 3


def test_llm_no_retry_on_4xx(monkeypatch):
    import genai_agent
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp(401)
    monkeypatch.setattr(genai_agent.requests, "post", fake_post)
    try:
        genai_agent._call_llm("x")
        assert False, "expected 401 to raise"
    except Exception:
        pass
    assert calls["n"] == 1  # no retry on 4xx


def test_llm_gives_up_after_3_attempts(monkeypatch):
    import genai_agent
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp(500)
    monkeypatch.setattr(genai_agent.requests, "post", fake_post)
    monkeypatch.setattr(genai_agent.time, "sleep", lambda s: None)
    try:
        genai_agent._call_llm("x")
        assert False, "expected 500 to raise"
    except Exception:
        pass
    assert calls["n"] == 3


# --------------------------------------------------------------------------
# Agent: humanized LLM errors (no raw exception dumps in the UI)
# --------------------------------------------------------------------------
def test_humanize_llm_error_variants():
    assert "404" in genai_agent._humanize_llm_error(Exception("404 Client Error: NOT FOUND for url: http://x/v1"))
    assert "DNS" in genai_agent._humanize_llm_error(
        Exception("NameResolutionError: Failed to resolve 'litellm.example' ([Errno -2])"))
    assert "connection" in genai_agent._humanize_llm_error(
        Exception("HTTPSConnectionPool(host='x', port=443): Max retries exceeded"))
    assert "timed out" in genai_agent._humanize_llm_error(Exception("The read operation timed out"))
    assert "JSON" in genai_agent._humanize_llm_error(Exception("Expecting value: line 1 column 1"))


# --------------------------------------------------------------------------
# Agent: offline fallback contract (demo survives LLM outage)
# --------------------------------------------------------------------------
def test_analyze_failure_forced_fallback_contract(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("Max retries exceeded with url: /v1/chat/completions")
    monkeypatch.setattr(genai_agent, "_call_llm", boom)
    monkeypatch.setattr(genai_agent, "LLM_ENDPOINT", "http://127.0.0.1:1/v1")

    out = genai_agent.analyze_failure(
        job_trace="AssertionError: DB pool exhausted",
        changed_files=["app/db/pool.py"],
        commit_msg="feat: x",
    )
    assert out["_source"] == "offline-fallback"
    assert out["confidence"] == 0.0
    assert "LLM" in out["root_cause"]
    assert "Max retries" not in out["root_cause"]  # humanized, not raw
    for k in ("root_cause", "confidence", "risk_score", "patch", "summary",
              "reasoning_steps", "validation_commands", "failure_category",
              "_llm_model", "_llm_latency_ms"):
        assert k in out


def test_analyze_failure_live_llm_path(monkeypatch):
    captured = {}

    def fake_llm(user_msg):
        captured["msg"] = user_msg
        return {"root_cause": "pool too small", "confidence": 0.9, "risk_score": 20,
                "patch": "diff", "summary": "raise pool"}
    monkeypatch.setattr(genai_agent, "_call_llm", fake_llm)

    out = genai_agent.analyze_failure(
        job_trace="AssertionError: DB pool exhausted: 5 < 6",
        changed_files=["app/db/pool.py"],
        commit_msg="feat",
        file_contents={"app/db/pool.py": "POOL_SIZE = 5"},
        git_diff="diff --git a/app/db/pool.py",
        stage_context="payments under load",
    )
    assert out["_source"] == "live-llm"
    # context actually reached the model
    assert "app/db/pool.py" in captured["msg"]
    assert "POOL_SIZE = 5" in captured["msg"]
    assert "payments under load" in captured["msg"]
    assert "DB pool exhausted" in captured["msg"]


# --------------------------------------------------------------------------
# Patch normalization (LLM path drift -> git apply still works)
# --------------------------------------------------------------------------
def test_normalize_patch_retargers_to_real_file():
    import webhook
    patch = (
        "diff --git a/app/db/pool.py b/app/db/pool.py\n"
        "--- a/app/db/pool.py\n"
        "+++ b/app/db/pool.py\n"
        "@@ -1 +1 @@\n"
        "-POOL_SIZE = 5\n"
        "+POOL_SIZE = 6\n"
    )
    norm = webhook._normalize_patch(patch, ["app/db/pool.py"])
    assert "--- a/app/db/pool.py" in norm and "+++ b/app/db/pool.py" in norm


def test_normalize_patch_no_op_when_paths_match():
    import webhook
    patch = "--- a/x.py\n+++ b/x.py\n@@\n-a\n+b\n"
    assert webhook._normalize_patch(patch, ["x.py"]) == patch


# --------------------------------------------------------------------------
# KPI accounting: no inflation, per-pipeline dedupe, measured values
# --------------------------------------------------------------------------
def _fresh_kpi_db(tmp_path):
    import webhook
    db = tmp_path / "kpis_test.db"
    webhook.DB_PATH = str(db)
    webhook.init_db()
    webhook._seen_green.clear()
    webhook._seen_red.clear()
    return webhook


def test_kpi_no_seed_and_dedupe(tmp_path):
    wh = _fresh_kpi_db(tmp_path)
    m0 = wh.get_metrics(project_id=999)
    assert m0["auto_fixes"] == 0 and m0["manual_fixes"] == 0
    assert m0["auto_fix_rate"] is None  # honest: nothing measured yet
    # same pipeline counted once, different pipelines counted twice
    wh._record_auto_fix(20, 101)
    wh._record_auto_fix(30, 101)  # duplicate -> ignored
    wh._record_auto_fix(25, 102)
    m1 = wh.get_metrics(project_id=999)
    assert m1["auto_fixes"] == 2
    assert m1["auto_fix_rate"] == 100  # 2/2 auto
    # fix duration accumulates (measured)
    wh._set_fix_seconds(120.0)
    wh._set_fix_seconds(300.0)
    assert wh.get_metrics(project_id=999)["last_fix_seconds"] == 300.0


def test_kpi_reset(tmp_path):
    wh = _fresh_kpi_db(tmp_path)
    wh._record_auto_fix(20, 101)
    wh._set_fix_seconds(500.0)
    wh.reset_demo()
    m = wh.get_metrics(project_id=999)
    assert m["auto_fixes"] == 0 and m["last_fix_seconds"] == 0.0
    assert m["auto_fix_rate"] is None


def test_kpi_releases_count_is_real_api_not_poll(tmp_path):
    """releases_7d comes from GitLab (mock -> 0), never incremented per poll."""
    wh = _fresh_kpi_db(tmp_path)
    os.environ["GITLAB_MODE"] = "mock"
    m = wh.get_metrics(project_id=1)
    assert m["releases_7d"] == 0
    # simulate many polls of a green pipeline: number must not move
    for _ in range(5):
        wh.get_metrics(project_id=1)
    assert wh.get_metrics(project_id=1)["releases_7d"] == 0
    os.environ.pop("GITLAB_MODE", None)
