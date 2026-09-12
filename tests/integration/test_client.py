"""Integration: external API client must survive a transient timeout.

Deterministic, network-free: `requests.get` is monkeypatched so the FIRST
attempt always raises Timeout and the second succeeds. A client WITHOUT a
retry wrapper fails this test (the timeout propagates); the retried client
passes. This is the failure the GenAI agent triages in scenario
`missing_retry`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app"))

import requests  # noqa: E402
from client import fetch  # noqa: E402


def test_external_api_retries_on_timeout(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout("simulated 2s timeout on first attempt")
        r = requests.Response()
        r.status_code = 200
        r._content = b'{"ok": true}'
        return r

    monkeypatch.setattr(requests, "get", fake_get)
    result = fetch("https://api.example.com/orders", timeout=2)
    assert result["ok"] is True
    assert calls["n"] >= 2, "client did not retry after the first timeout"
