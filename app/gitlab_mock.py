"""Lightweight in-memory GitLab emulator for the CI/CD GenAI demo.

Mirrors the subset of the GitLab v4 REST surface the demo uses so the full
auto-fix flow (fail -> analyze -> approve -> MR -> green) runs in <5s with
zero external dependencies. Selected via GITLAB_MODE=mock in webhook.py.
"""
import os
import re
import time
import json
from scenarios import get_scenario

# ---- in-memory state (per project id) ----
_STATE = {}

def _proj(pid):
    pid = str(pid)
    if pid not in _STATE:
        _STATE[pid] = {"next_pid": 1, "pipelines": [], "mrs": [], "current_scenario": None}
    return _STATE[pid]

def seed_failing_pipeline(pid, ref: str = "main", scenario_id=None):
    """Create a pipeline that fails at the integration stage (the showcase)."""
    p = _proj(pid)
    scenario = get_scenario(scenario_id)
    p["current_scenario"] = scenario
    
    pid_id = p["next_pid"]; p["next_pid"] += 1
    jobs = [
        {"id": pid_id * 10 + 1, "pipeline_id": pid_id, "stage": "build",
         "name": "build", "status": "success", "trace": "docker build ... ok"},
        {"id": pid_id * 10 + 2, "pipeline_id": pid_id, "stage": "test",
         "name": "unit-test", "status": "success", "trace": "pytest -q ... 0 failed"},
        {"id": pid_id * 10 + 3, "pipeline_id": pid_id, "stage": "integration",
         "name": "integration-test", "status": "failed", "trace": scenario["trace"],
         "changed_files": [scenario["changed_file"]]},
    ]
    pipe = {"id": pid_id, "ref": ref, "status": "failed", "created_at": time.time(), "scenario_id": scenario["id"]}
    p["pipelines"].append({
        "pipeline": pipe, 
        "jobs": jobs, 
        "trace": scenario["trace"],
        "scenario": scenario,
        "file_before": scenario["file_before"],
        "file_after": scenario["file_after"],
        "git_diff": scenario["git_diff"]
    })
    return pipe

def trigger_pipeline(pid, ref: str = "main"):
    """Start a fresh pipeline (replays the failing flow for the demo)."""
    return seed_failing_pipeline(pid, ref)

def poll_pipeline_state(pid=None):
    p = _proj(pid or os.getenv("GITLAB_PROJECT_ID", "1"))
    if not p["pipelines"]:
        return {"pipeline": None, "jobs": [], "failed_jobs": [], "trace": "", "changed_files": [], "scenario": None}
    last = p["pipelines"][-1]
    jobs = last["jobs"]
    failed = [j for j in jobs if j.get("status") == "failed"]
    changed = []
    for j in failed:
        changed.extend(j.get("changed_files", []))
    scenario = last.get("scenario")
    return {
        "pipeline": last["pipeline"],
        "jobs": jobs,
        "failed_jobs": failed,
        "trace": last["trace"][:6000],
        "changed_files": changed,
        "scenario": scenario,
        "file_before": last.get("file_before"),
        "file_after": last.get("file_after"),
        "git_diff": last.get("git_diff"),
        "manual_baseline": {
            "triage_minutes": scenario["manual_triage_minutes"] if scenario else 45,
            "steps": scenario["manual_steps"] if scenario else ["Download logs", "Manual analysis", "Create PR"],
            "auto_minutes": scenario["auto_triage_minutes"] if scenario else 5
        }
    }

def get_job_trace(pid, job_id):
    p = _proj(pid)
    for entry in reversed(p["pipelines"]):
        for j in entry["jobs"]:
            if j["id"] == job_id:
                return j.get("trace", "")
    return ""

def create_merge_request(pid, branch: str, title: str, patch: str, target: str = "main"):
    """Record the auto-fix MR and flip the latest pipeline to green (mock)."""
    p = _proj(pid)
    files = re.findall(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE) or [p["current_scenario"]["changed_file"] if p.get("current_scenario") else "(patch attached)"]
    mr = {
        "iid": len(p["mrs"]) + 1,
        "title": title,
        "source_branch": branch,
        "target_branch": target,
        "web_url": f"http://mock.gitlab/{pid}/-/merge_requests/{len(p['mrs']) + 1}",
        "state": "opened",
    }
    p["mrs"].append(mr)
    if p["pipelines"]:
        p["pipelines"][-1]["pipeline"]["status"] = "success"
        for j in p["pipelines"][-1]["jobs"]:
            if j["status"] == "failed":
                j["status"] = "success"
    return {
        "mr_url": mr["web_url"],
        "mr_iid": mr["iid"],
        "branch": branch,
        "applied": True,
        "patch_applied": True,
        "files": files,
        "title": title,
    }

# Minimal api_get/api_post so webhook's generic calls work in mock mode.
def api_get(path: str, params: dict = None):
    m = re.search(r"projects/[^/]+/pipelines", path)
    if m and "jobs" not in path:
        pid = re.search(r"projects/([^/]+)/", path).group(1)
        p = _proj(pid)
        return [e["pipeline"] for e in p["pipelines"]] or []
    return []

def api_post(path: str, data: dict = None):
    m = re.search(r"projects/([^/]+)/pipeline", path)
    if m:
        return trigger_pipeline(m.group(1), (data or {}).get("ref", "main"))
    return {}
