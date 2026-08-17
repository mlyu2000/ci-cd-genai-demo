"""GitLab webhook ingestion + SSE event store (real GitLab mode)."""
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

_lock = threading.Lock()
_listeners = []

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
    # Pipeline / job status updates
    if kind == "pipeline":
        attrs = data.get("object_attributes", {})
        pid = attrs.get("id")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO pipelines (id, ref, status, created) VALUES (?,?,?,?)",
                         (pid, attrs.get("ref"), attrs.get("status"), time.time()))
        emit({"type": "pipeline", "id": pid, "status": attrs.get("status"), "ref": attrs.get("ref")})
    elif kind == "build" or kind == "job":
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

def get_gitlab_job_trace(project_id: int, job_id: int) -> str:
    """Fetch job trace via GitLab API."""
    try:
        r = requests.get(f"{GITLAB_URL}/api/v4/projects/{project_id}/jobs/{job_id}/trace",
                         headers={"PRIVATE-TOKEN": GITLAB_TOKEN}, timeout=30)
        return r.text if r.ok else ""
    except Exception:
        return ""

def create_merge_request(project_id: int, branch: str, title: str, patch: str) -> dict:
    """Create an MR with the auto-fix patch (real GitLab)."""
    try:
        r = requests.post(f"{GITLAB_URL}/api/v4/projects/{project_id}/merge_requests",
                          headers={"PRIVATE-TOKEN": GITLAB_TOKEN},
                          json={"source_branch": branch, "target_branch": "main",
                                "title": title, "description": patch[:2000]}, timeout=30)
        return r.json() if r.ok else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}

def recent_events(limit: int = 50):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT ts, kind, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ts": r[0], "kind": r[1], "payload": json.loads(r[2])} for r in rows]

if __name__ == "__main__":
    init_db()
    print("DB initialized at", DB_PATH)
