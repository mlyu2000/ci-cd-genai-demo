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
