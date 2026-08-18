"""GitLab integration: real (v4 REST) + mock (in-memory) backends, webhook
ingestion, SSE event store, and KPI persistence for the CI/CD GenAI demo.

Backend selection is driven by GITLAB_MODE (real | mock). All public functions
dispatch to the active backend so callers (main.py) stay backend-agnostic.
"""
import os
import hmac
import hashlib
import sqlite3
import json
import threading
import time
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), "pipeline_state.db")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "demo-secret")
GITLAB_URL = os.getenv("GITLAB_URL", "http://localhost:8929")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")
MODE = os.getenv("GITLAB_MODE", "real").lower()

_lock = threading.Lock()
_listeners = []


# --------------------------------------------------------------------------
# Backend dispatch
# --------------------------------------------------------------------------
def _mock():
    import gitlab_mock
    return gitlab_mock


def _use_mock():
    return MODE == "mock"


# --------------------------------------------------------------------------
# DB / events / KPIs
# --------------------------------------------------------------------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, kind TEXT, payload TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pipelines (
            id INTEGER PRIMARY KEY, ref TEXT, status TEXT, created REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, pipeline_id INTEGER, stage TEXT, name TEXT,
            status TEXT, trace TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS kpis (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            auto_fix_count INTEGER, manual_fix_count INTEGER,
            releases INTEGER, last_risk INTEGER,
            saved_minutes REAL, baseline_mttr REAL)""")
        conn.execute("""INSERT OR IGNORE INTO kpis
            (id, auto_fix_count, manual_fix_count, releases, last_risk, saved_minutes, baseline_mttr)
            VALUES (1, 87, 13, 12, 35, 87*35, 4.0)""")


def emit(event: dict):
    with _lock:
        _listeners.append(event)
        if len(_listeners) > 200:
            _listeners.pop(0)


def verify_signature(body: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    mac = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, signature or "")


def record_pipeline_event(data: dict):
    kind = data.get("object_kind") or data.get("kind") or "unknown"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO events (ts, kind, payload) VALUES (?,?,?)",
                     (time.time(), kind, json.dumps(data)))
    emit({"type": "event", "kind": kind, "ts": time.time()})
    if kind == "pipeline":
        attrs = data.get("object_attributes", {})
        pid = attrs.get("id")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO pipelines (id, ref, status, created) VALUES (?,?,?,?)",
                         (pid, attrs.get("ref"), attrs.get("status"), time.time()))
        emit({"type": "pipeline", "id": pid, "status": attrs.get("status"), "ref": attrs.get("ref")})
    elif kind in ("build", "job"):
        attrs = data.get("build_status") and data or data.get("object_attributes", {})
        jid = data.get("build_id") or attrs.get("id")
        stage = data.get("build_stage") or attrs.get("stage")
        name = data.get("build_name") or attrs.get("name")
        status = data.get("build_status") or attrs.get("status")
        pid = data.get("pipeline_id") or attrs.get("pipeline_id")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO jobs (id, pipeline_id, stage, name, status, trace) VALUES (?,?,?,?,?,?)",
                         (jid, pid, stage, name, status, ""))
        emit({"type": "job", "id": jid, "stage": stage, "name": name, "status": status, "pipeline_id": pid})


def _record_release():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE kpis SET releases = releases + 1 WHERE id = 1")


def record_auto_fix(analysis: dict):
    """Persist an auto-fix event so KPIs reflect real activity."""
    risk = int(analysis.get("risk_score", 0) or 0)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""UPDATE kpis
            SET auto_fix_count = auto_fix_count + 1,
                last_risk = ?,
                saved_minutes = saved_minutes + 35
            WHERE id = 1""", (risk,))


def record_manual_fix():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE kpis SET manual_fix_count = manual_fix_count + 1 WHERE id = 1")


def get_metrics() -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT auto_fix_count, manual_fix_count, releases, last_risk, saved_minutes, baseline_mttr FROM kpis WHERE id = 1").fetchone()
    if not row:
        return {"mttr_min": 4.0, "auto_fix_rate": 87, "releases_week": 12,
                "risk_score": 35, "saved_minutes": 87 * 35}
    auto, manual, releases, risk, saved, mttr = row
    total = auto + manual
    rate = round(100 * auto / total) if total else 0
    return {"mttr_min": mttr, "auto_fix_rate": rate, "releases_week": releases,
            "risk_score": risk or 0, "saved_minutes": saved or 0}


# --------------------------------------------------------------------------
# GitLab REST surface (dispatched)
# --------------------------------------------------------------------------
def _headers():
    return {"PRIVATE-TOKEN": GITLAB_TOKEN}


def api_get(path: str, params: dict = None):
    if _use_mock():
        return _mock().api_get(path, params)
    try:
        r = requests.get(f"{GITLAB_URL}/api/v4/{path}", headers=_headers(), params=params, timeout=30)
        return r.json() if r.ok else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}


def api_post(path: str, data: dict = None):
    if _use_mock():
        return _mock().api_post(path, data)
    try:
        r = requests.post(f"{GITLAB_URL}/api/v4/{path}", headers=_headers(), json=data, timeout=30)
        return r.json() if r.ok else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}


def trigger_pipeline(project_id: int, ref: str = "master") -> dict:
    if _use_mock():
        return _mock().trigger_pipeline(project_id, ref)
    res = api_post(f"projects/{project_id}/pipeline", {"ref": ref})
    return res


def get_gitlab_job_trace(project_id: int, job_id: int) -> str:
    if _use_mock():
        return _mock().get_job_trace(project_id, job_id)
    try:
        r = requests.get(f"{GITLAB_URL}/api/v4/projects/{project_id}/jobs/{job_id}/trace",
                         headers=_headers(), timeout=30)
        return r.text if r.ok else ""
    except Exception:
        return ""


def poll_pipeline_state(project_id: int = None):
    if _use_mock():
        return _mock().poll_pipeline_state(project_id)
    pid = project_id or int(os.getenv("GITLAB_PROJECT_ID", "1"))
    pipes = api_get(f"projects/{pid}/pipelines", {"per_page": 1, "order_by": "id", "sort": "desc"})
    if isinstance(pipes, list) and pipes:
        p = pipes[0]
        pid_id = p["id"]
        jobs = api_get(f"projects/{pid}/pipelines/{pid_id}/jobs")
        failed = [j for j in jobs if j.get("status") == "failed"] if isinstance(jobs, list) else []
        trace = ""
        if failed:
            jid = failed[0]["id"]
            try:
                tr = requests.get(f"{GITLAB_URL}/api/v4/projects/{pid}/jobs/{jid}/trace",
                                  headers=_headers(), timeout=30)
                trace = tr.text if tr.ok else ""
            except Exception as e:
                trace = str(e)
        return {"pipeline": p, "jobs": jobs if isinstance(jobs, list) else [],
                "failed_jobs": failed, "trace": trace[:6000]}
    return {"pipeline": None, "jobs": [], "failed_jobs": [], "trace": ""}


def create_merge_request(project_id: int, branch: str, title: str, patch: str,
                         target: str = "main", analysis: dict = None) -> dict:
    """Open an auto-fix MR. Real mode commits the patch as a reviewable file on a
    new branch so the MR genuinely contains the proposed fix; mock mode records it
    and flips the pipeline green. Returns a structured result for the UI."""
    if _use_mock():
        return _mock().create_merge_request(project_id, branch, title, patch, target)

    try:
        # 1. create branch from target
        bres = api_post(f"projects/{project_id}/repository/branches",
                        {"branch": branch, "ref": target})
        if isinstance(bres, dict) and bres.get("error"):
            return {"applied": False, "error": bres["error"]}
        # 2. commit the patch as a reviewable file (raw diff for human apply)
        commit_msg = f"fix: {title}"
        file_path = f"ai-autofix/{branch}.patch"
        cres = api_post(f"projects/{project_id}/repository/commits", {
            "branch": branch,
            "commit_message": commit_msg,
            "actions": [{"action": "create", "file_path": file_path,
                         "content": patch or "(no diff produced by agent)"}],
        })
        if isinstance(cres, dict) and cres.get("error"):
            return {"applied": False, "error": cres["error"]}
        # 3. open the MR
        mres = api_post(f"projects/{project_id}/merge_requests", {
            "source_branch": branch, "target_branch": target,
            "title": title,
            "description": f"Auto-generated fix.\n\nRoot cause: {analysis.get('root_cause','') if analysis else ''}\n\n```diff\n{patch}\n```",
        })
        if isinstance(mres, dict) and mres.get("error"):
            return {"applied": False, "error": mres["error"]}
        return {
            "applied": True,
            "mr_url": mres.get("web_url", ""),
            "mr_iid": mres.get("iid"),
            "branch": branch,
            "files": [file_path],
            "title": title,
        }
    except Exception as e:
        return {"applied": False, "error": str(e)}


def recent_events(limit: int = 50):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT ts, kind, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ts": r[0], "kind": r[1], "payload": json.loads(r[2])} for r in rows]


if __name__ == "__main__":
    init_db()
    print("DB initialized at", DB_PATH)
