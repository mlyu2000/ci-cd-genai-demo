"""GitLab integration: real (v4 REST) + mock (in-memory) backends, webhook
ingestion, SSE event store, and MEASURED KPIs for the CI/CD GenAI demo.

KPIs are honest: no fabricated seeds, no per-poll inflation. Every number is
derived from this session's real events (pipelines observed, auto-fixes
applied, measured fix durations) or from the live GitLab project (releases in
the last 7 days).
"""
import os
import hmac
import hashlib
import sqlite3
import json
import shutil
import subprocess
import tempfile
import threading
import time
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), "pipeline_state.db")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "demo-secret")
GITLAB_URL = os.getenv("GITLAB_URL", "http://localhost:8929")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")
MODE = os.getenv("GITLAB_MODE", "real").lower()
PROJECT_ID = int(os.getenv("GITLAB_PROJECT_ID", "1"))

_lock = threading.Lock()
_listeners = []
_seen_green = set()
_seen_red = set()


# --------------------------------------------------------------------------
# Backend dispatch
# --------------------------------------------------------------------------
def _mock():
    import gitlab_mock
    return gitlab_mock


def _use_mock():
    return MODE == "mock"


# --------------------------------------------------------------------------
# DB / events
# --------------------------------------------------------------------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, kind TEXT, payload TEXT)""")
        # Migrate the old fabricated-KPI schema -> measured schema. The old numbers
        # were seeds/inflated (not measurements), so a clean slate is intentional.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(kpis)").fetchall()]
        if cols and "auto_fixes" not in cols:
            conn.execute("DROP TABLE kpis")
            cols = []
        if not cols:
            conn.execute("""CREATE TABLE kpis (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                auto_fixes INTEGER, manual_fixes INTEGER,
                fixed_pipeline_ids TEXT, fix_seconds REAL,
                last_risk INTEGER)""")
            conn.execute("""INSERT INTO kpis
                (id, auto_fixes, manual_fixes, fixed_pipeline_ids, fix_seconds, last_risk)
                VALUES (1, 0, 0, '[]', 0.0, 0)""")


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


def _set_fix_seconds(seconds: float):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE kpis SET fix_seconds = ? WHERE id = 1", (float(seconds),))


def _record_auto_fix(risk: int, pipeline_id: int):
    """Count one auto-fix, deduped per pipeline id."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT fixed_pipeline_ids FROM kpis WHERE id = 1").fetchone()
        fixed = json.loads(row[0] or "[]")
        if str(pipeline_id) in [str(x) for x in fixed]:
            return
        fixed.append(pipeline_id)
        conn.execute("""UPDATE kpis
            SET auto_fixes = auto_fixes + 1,
                fixed_pipeline_ids = ?, last_risk = ?
            WHERE id = 1""", (json.dumps(fixed), int(risk)))


def record_manual_fix():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE kpis SET manual_fixes = manual_fixes + 1 WHERE id = 1")


def reset_demo():
    """Wipe session KPIs (button: Demo Reset). Leaves history intact."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""UPDATE kpis
            SET auto_fixes = 0, manual_fixes = 0, fixed_pipeline_ids = '[]',
                fix_seconds = 0.0, last_risk = 0
            WHERE id = 1""")
    _seen_green.clear()
    _seen_red.clear()
    emit({"type": "reset", "ts": time.time()})


# ---- measured KPIs --------------------------------------------------------
def _releases_last_7d(project_id: int) -> int:
    """Green pipelines in the last 7 days (real GitLab), 0 in mock mode."""
    if _use_mock():
        return 0
    try:
        since = time.time() - 7 * 86400
        pipes = api_get(f"projects/{project_id}/pipelines",
                        {"per_page": 100, "order_by": "id", "sort": "desc"})
        if not isinstance(pipes, list):
            return 0
        return sum(1 for p in pipes
                   if p.get("status") == "success"
                   and p.get("updated_at", "") and _ts_of(p) >= since)
    except Exception:
        return 0


def _ts_of(pipe: dict) -> float:
    """Parse a GitLab ISO-8601 timestamp to epoch seconds (UTC)."""
    from datetime import datetime, timezone
    s = (pipe.get("updated_at") or pipe.get("created_at") or "")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def get_metrics(project_id: int = None) -> dict:
    """Honest KPIs: measured from this session + live GitLab data."""
    pid = project_id or PROJECT_ID
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT auto_fixes, manual_fixes, fix_seconds, last_risk FROM kpis WHERE id = 1").fetchone()
    auto, manual, fix_seconds, risk = (row if row else (0, 0, 0.0, 0))
    total = auto + manual
    rate = round(100 * auto / total) if total else None
    releases = _releases_last_7d(pid)
    hours_saved = round((fix_seconds or 0) / 3600.0, 2)
    return {
        "auto_fix_rate": rate,                 # None until first fix happens
        "auto_fixes": auto, "manual_fixes": manual,
        "releases_7d": releases,               # measured: green pipelines, last 7 days
        "risk_score": int(risk or 0),
        "last_fix_seconds": round(fix_seconds or 0.0, 1),
        "hours_saved_session": hours_saved,    # measured: sum of fix durations this session
        "observed": True,                      # UI: label values as measured
    }


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


def api_put(path: str, data: dict = None):
    if _use_mock():
        return _mock().api_post(path, data)
    try:
        r = requests.put(f"{GITLAB_URL}/api/v4/{path}", headers=_headers(), json=data, timeout=30)
        return r.json() if r.ok else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}


def get_file_raw(project_id: int, path: str, ref: str) -> str:
    """Raw file content at a given ref — feeds the agent the failing source code."""
    if _use_mock():
        return ""
    if not ref:
        return ""
    from urllib.parse import quote
    try:
        r = requests.get(
            f"{GITLAB_URL}/api/v4/projects/{project_id}/repository/files/{quote(path, safe='')}/raw",
            headers=_headers(), params={"ref": ref}, timeout=30)
        return r.text if r.ok else ""
    except Exception:
        return ""


def get_commit_diff(project_id: int, sha: str) -> str:
    """Unified diff of a commit (GitLab API) as a patch string for the agent."""
    if _use_mock():
        return ""
    if not sha:
        return ""
    try:
        d = api_get(f"projects/{project_id}/repository/commits/{sha}/diff")
        if not isinstance(d, list) or not d:
            return ""
        parts = []
        for x in d:
            parts.append(f"diff --git a/{x.get('old_path', '')} b/{x.get('new_path', '')}")
            parts.append(x.get("diff", ""))
        return "\n".join(parts)
    except Exception:
        return ""


def trigger_pipeline(project_id: int, ref: str = "master") -> dict:
    if _use_mock():
        return _mock().trigger_pipeline(project_id, ref)
    return api_post(f"projects/{project_id}/pipeline", {"ref": ref})


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
    pid = project_id or PROJECT_ID
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
                "failed_jobs": failed, "trace": trace[-6000:]}
    return {"pipeline": None, "jobs": [], "failed_jobs": [], "trace": ""}


def get_pipeline_status(project_id: int, pipeline_id: int) -> dict:
    """Live status + finished_at of one pipeline (for fix-duration measurement)."""
    if _use_mock():
        return {}
    return api_get(f"projects/{project_id}/pipelines/{pipeline_id}")


def get_mr(project_id: int, mr_iid: int) -> dict:
    if _use_mock():
        return {}
    return api_get(f"projects/{project_id}/merge_requests/{mr_iid}")


# --------------------------------------------------------------------------
# Auto-fix: real patch application + MR + (optional) merge
# --------------------------------------------------------------------------
def _git_remote_for_gitlab(repo_path: str) -> str:
    """Pick the git remote that points at our GitLab (not GitHub)."""
    try:
        import subprocess as _sp
        host = (GITLAB_URL or "").split("//")[-1].split("/")[0]
        out = _sp.run(["git", "remote", "-v"], cwd=repo_path, capture_output=True,
                      timeout=30).stdout.decode()
        for line in out.splitlines():
            # "gitlab\thttp://root:token@host/root/x.git (push)"
            name = line.split("\t")[0]
            url = line.split("\t")[1] if "\t" in line else ""
            if host and host in url:
                return name
        # fallback: a remote named gitlab, else origin
        names = [l.split("\t")[0] for l in out.splitlines() if l.strip()]
        return "gitlab" if "gitlab" in names else "origin"
    except Exception:
        return "origin"


def _local_workdir(repo_path: str, worktree: str) -> bool:
    """git worktree add for a clean copy of the repo (no side effects on cwd)."""
    if not repo_path or not os.path.isdir(repo_path):
        return False
    try:
        subprocess.run(["git", "worktree", "add", "--detach", worktree, "HEAD"],
                       cwd=repo_path, check=True, capture_output=True, timeout=60)
        return True
    except Exception:
        return False


def _git_worktree_remove(repo_path: str, worktree: str):
    try:
        subprocess.run(["git", "worktree", "remove", "--force", worktree],
                       cwd=repo_path, check=False, capture_output=True, timeout=60)
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)
    try:
        subprocess.run(["git", "worktree", "prune"], cwd=repo_path,
                       check=False, capture_output=True, timeout=30)
    except Exception:
        pass


def _normalize_patch(patch: str, changed_files: list) -> str:
    """Normalize LLM patch paths so `git apply` can find the files.

    LLMs commonly emit 'app/db/pool.py' when the test file is
    'tests/integration/test_integration.py' — remap a/ b/ headers to the
    real files when the patch targets files that don't match the repo.
    """
    if not patch:
        return patch
    import re
    trailing_nl = patch.endswith("\n")
    lines = patch.splitlines()
    # find every file referenced in the patch
    files_in_patch = set(re.findall(r"^diff --git a/(\S+) b/(\S+)", "\n".join(lines), re.M))
    files_in_patch = {b for _a, b in files_in_patch} | {a for a, _b in files_in_patch}
    # remap: if a patch file isn't in changed_files but there's exactly one
    # changed file, retarget the hunk file headers to it
    if changed_files:
        for old in list(files_in_patch):
            if old not in changed_files:
                new = changed_files[0]
                for i, ln in enumerate(lines):
                    if ln.startswith(f"--- a/{old}") or ln.startswith(f"--- {old}"):
                        lines[i] = f"--- a/{new}"
                    elif ln.startswith(f"+++ b/{old}") or ln.startswith(f"+++ {old}"):
                        lines[i] = f"+++ b/{new}"
                files_in_patch.discard(old)
    out = "\n".join(lines)
    if trailing_nl and not out.endswith("\n"):
        out += "\n"
    return out


def create_merge_request(project_id: int, branch: str, title: str, patch: str,
                         target: str = "master", analysis: dict = None,
                         repo_path: str = "", pipeline_id: int = 0,
                         changed_files: list = None) -> dict:
    """Apply the patch for real and open an MR whose pipeline verifies the fix.

    Real mode: git worktree -> git apply -> push branch -> MR. If the patch
    cannot be applied (LLM hallucinated a hunk), we fall back to committing
    the patch as a reviewable .patch file and say so honestly (applied=False
    with a clear reason) instead of claiming success.
    """
    if _use_mock():
        return _mock().create_merge_request(project_id, branch, title, patch, target)

    analysis = analysis or {}
    changed_files = changed_files or analysis.get("files_touched") or []
    worktree = tempfile.mkdtemp(prefix="autofix-")
    applied = False
    files_committed = []
    error = ""
    try:
        # 1. materialize the fix in a clean worktree and push it as a branch
        if patch and repo_path and _local_workdir(repo_path, worktree):
            norm = _normalize_patch(patch, changed_files)
            r = subprocess.run(["git", "apply", "--whitespace=fix", "-"],
                               cwd=worktree, input=norm.encode(),
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                subprocess.run(["git", "add", "-A"], cwd=worktree, check=False,
                               capture_output=True, timeout=60)
                st = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                    cwd=worktree, capture_output=True,
                                    timeout=60).stdout.decode().split()
                if st:
                    files_committed = st
                    subprocess.run(["git", "commit", "-m", f"fix: {title}"],
                                   cwd=worktree, check=True, capture_output=True,
                                   timeout=60)
                    remote = _git_remote_for_gitlab(repo_path)
                    push_r = subprocess.run(
                        ["git", "push", remote, f"HEAD:refs/heads/{branch}"],
                        cwd=worktree, capture_output=True, timeout=120,
                        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
                    if push_r.returncode != 0:
                        _git_worktree_remove(repo_path, worktree)
                        shutil.rmtree(worktree, ignore_errors=True)
                        return {"applied": False, "patch_applied": False,
                                "error": "git push failed: " + (push_r.stderr.decode() or "")[:300]}
                    applied = True
            else:
                error = "git apply failed: " + (r.stderr.decode() or "context mismatch")[:300]

        # 2. honest fallback: commit the patch as a reviewable file
        if not applied:
            bres = api_post(f"projects/{project_id}/repository/branches",
                            {"branch": branch, "ref": target})
            if isinstance(bres, dict) and bres.get("error"):
                return {"applied": False, "error": bres["error"]}
            file_path = f"ai-autofix/{branch}.patch"
            cres = api_post(f"projects/{project_id}/repository/commits", {
                "branch": branch,
                "commit_message": f"fix: {title} (review — auto-apply {error or 'skipped'})",
                "actions": [{"action": "create", "file_path": file_path,
                             "content": (patch or "(no diff produced by agent)") +
                                         (f"\n\n# auto-apply failed: {error}\n" if error else "")}],
            })
            if isinstance(cres, dict) and cres.get("error"):
                return {"applied": False, "error": cres["error"]}
            files_committed = [file_path]

        # 3. open the MR
        mres = api_post(f"projects/{project_id}/merge_requests", {
            "source_branch": branch, "target_branch": target,
            "title": title,
            "remove_source_branch": True,
            "squash": False,
            "description": (
                f"{'Applied' if applied else 'Proposed (not auto-applied)'} by the GenAI auto-fix agent.\n\n"
                f"**Root cause:** {analysis.get('root_cause','')}\n"
                f"**Confidence:** {analysis.get('confidence')}  **Risk:** {analysis.get('risk_score')}/100\n"
                f"**Pipeline that triggered:** #{pipeline_id}\n\n"
                f"```diff\n{patch}\n```\n"
                + (f"\n> ⚠️ Auto-apply failed (`git apply` rejected the hunk): {error}\n"
                   "   This MR attaches the patch for a human to apply.\n" if error else "")
            ),
        })
        if isinstance(mres, dict) and mres.get("error"):
            return {"applied": False, "error": mres["error"]}
        return {
            "applied": applied,
            "patch_applied": applied,
            "apply_error": error,
            "mr_url": mres.get("web_url", ""),
            "mr_iid": mres.get("iid"),
            "branch": branch,
            "files": files_committed,
            "title": title,
        }
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode()[:300] if e.stderr else str(e)
        return {"applied": False, "error": f"git step failed: {detail}"}
    except Exception as e:
        return {"applied": False, "error": str(e)}
    finally:
        if os.path.isdir(worktree):
            _git_worktree_remove(repo_path, worktree)
            shutil.rmtree(worktree, ignore_errors=True)


def merge_merge_request(project_id: int, mr_iid: int, sha: str = "") -> dict:
    """Merge an MR (real GitLab). sha guards against merging a stale version."""
    if _use_mock():
        return {"merged": True, "mr_iid": mr_iid}
    return api_put(f"projects/{project_id}/merge_requests/{mr_iid}/merge",
                   {"should_remove_source_branch": True,
                    **({"sha": sha} if sha else {})})


def recent_events(limit: int = 50):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT ts, kind, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ts": r[0], "kind": r[1], "payload": json.loads(r[2])} for r in rows]


if __name__ == "__main__":
    init_db()
    print("DB initialized at", DB_PATH)
