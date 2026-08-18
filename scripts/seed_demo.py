#!/usr/bin/env python3
"""Seed a failing pipeline on demand (real GitLab or mock) for the demo.

Usage:
    python scripts/seed_demo.py            # mock mode (default)
    GITLAB_MODE=real python scripts/seed_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import webhook  # dispatches real vs mock via GITLAB_MODE


def main():
    pid = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    mode = os.getenv("GITLAB_MODE", "real").lower()
    if mode == "mock":
        import gitlab_mock
        pipe = gitlab_mock.seed_failing_pipeline(pid)
        print(f"[mock] seeded failing pipeline #{pipe['id']} ref={pipe['ref']} status={pipe['status']}")
    else:
        # Real GitLab: trigger a pipeline; the flaky integration stage fails ~70%.
        res = webhook.trigger_pipeline(pid, os.getenv("GITLAB_REF", "master"))
        print(f"[real] triggered pipeline: {res}")


if __name__ == "__main__":
    main()
