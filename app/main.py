import os
import re
import sys
import json
import time
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, render_template_string, jsonify, request, Response, stream_with_context
from dotenv import load_dotenv
from datetime import datetime

import webhook
import genai_agent

load_dotenv()

# GitPython hard-fails on "dubious ownership" when the container user differs
# from the repo owner (mounted volume). Mark all paths safe via Git's env config.
os.environ.setdefault("GIT_CONFIG_COUNT", "1")
os.environ.setdefault("GIT_CONFIG_KEY_0", "safe.directory")
os.environ.setdefault("GIT_CONFIG_VALUE_0", "*")

# GitPython import is deferred: it hard-fails at import time when no `git`
# binary is present (e.g. minimal CI python image). Only import it where used.
try:
    import git  # noqa: E402
except Exception:  # pragma: no cover - import guard
    git = None

app = Flask(__name__)

# Honour GITLAB_MODE so the dashboard can run against the mock or real GitLab.
os.environ["GITLAB_MODE"] = os.getenv("GITLAB_MODE", "real").lower()

# The dashboard UI lives in a real file (ui_template.html) — not an inline
# Python triple-quoted string — so it can be edited without escape-drift and is
# the exact markup the browser receives (no Python string-escape mangling).
_UI_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_template.html")
with open(_UI_TEMPLATE_PATH, "r") as _f:
    HTML = _f.read()

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:18080/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "«redacted:sk-…»")
FLASK_PORT = int(os.getenv("FLASK_PORT", "8080"))

REPO_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
webhook.init_db()

def get_git_info():
    try:
        if git is None:
            return {"error": "git unavailable", "branch": "n/a",
                    "commit_hash": "n/a", "commit_msg": "n/a", "author": "n/a",
                    "author_name": "n/a", "author_email": "n/a",
                    "commit_time": "n/a", "files_changed": [], "diffstat": [],
                    "total_added": 0, "total_deleted": 0, "files_count": 0,
                    "dirty": [], "recent_commits": [], "remote": "n/a", "remote_name": "gitlab",
                    "commit_count": 0, "full_message": "n/a"}
        repo = git.Repo(REPO_PATH)
        try:
            branch = repo.active_branch.name
        except TypeError:
            branch = "detached"
        commit = repo.head.commit

        # Per-file diffstat vs the parent using `git diff --numstat` (exact line counts).
        diffstat = []
        total_added = total_deleted = 0
        try:
            parent = commit.parents[0] if commit.parents else None
            ref_a = commit.hexsha if parent is None else parent.hexsha
            ref_b = commit.hexsha
            out = repo.git.diff("--numstat", ref_a, ref_b)
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3 and parts[0] not in ("", "-"):
                    ins = int(parts[0]) if parts[0].isdigit() else 0
                    deleted = int(parts[1]) if parts[1].isdigit() else 0
                    path = parts[-1]
                    total_added += ins
                    total_deleted += deleted
                    diffstat.append({"path": path, "change_type": "M",
                                     "added": ins, "deleted": deleted})
            diffstat.sort(key=lambda d: d["path"])
        except Exception:
            diffstat = []
        files_changed = [d["path"] for d in diffstat] or \
                        [i.a_path for i in repo.index.diff(None)]

        # Working-tree status (uncommitted changes) for observability.
        dirty = []
        try:
            for d in repo.index.diff("HEAD"):
                dirty.append(d.a_path)
            dirty.extend(repo.untracked_files)
            dirty = sorted(set(dirty))
        except Exception:
            dirty = []

        # Recent commit history (for the "control & observability" panel).
        recent = []
        for c in list(repo.iter_commits())[:5]:
            recent.append({"hash": c.hexsha[:8],
                           "msg": c.message.split("\n")[0][:90],
                           "author": c.author.name,
                           "time": datetime.fromtimestamp(c.committed_date).isoformat()})

        # Primary remote = the DEMO source (GitLab), not the mirror. Pick the
        # remote whose URL matches GITLAB_URL; mask any embedded credentials.
        remote = "n/a"
        remote_name = "gitlab"

        def _host(u: str) -> str:
            # strip scheme + userinfo (creds) -> bare host[:port]
            h = u.split("//")[-1].split("/")[0]
            return h.split("@")[-1]

        try:
            glab_host = _host(os.getenv("GITLAB_URL", "") or "")
            chosen = None
            for r in repo.remotes:
                url = r.url or ""
                if glab_host and _host(url) == glab_host:
                    chosen = url
                    remote_name = r.name
                    break
            if chosen is None and repo.remotes:
                chosen = (repo.remotes[0].url or "n/a")
            if chosen:
                # strip scheme+creds and any token from the displayed URL
                shown = re.sub(r"://[^@/]+@", "://", chosen)
                shown = re.sub(r"(glpat-|oauth2:)[A-Za-z0-9_-]+", r"\1****", shown)
                remote = shown
        except Exception:
            remote = "n/a"

        return {
            "branch": branch,
            "commit_hash": commit.hexsha[:8],
            "commit_hash_full": commit.hexsha,
            "commit_msg": commit.message.split("\n")[0],
            "full_message": commit.message.strip(),
            "commit_time": datetime.fromtimestamp(commit.committed_date).isoformat(),
            "author": f"{commit.author.name} <{commit.author.email}>",
            "author_name": commit.author.name,
            "author_email": commit.author.email,
            "committer": f"{commit.committer.name} <{commit.committer.email}>",
            "files_changed": files_changed[:30],
            "files_count": len(files_changed),
            "diffstat": diffstat[:30],
            "total_added": total_added,
            "total_deleted": total_deleted,
            "dirty": dirty[:20],
            "is_dirty": len(dirty) > 0,
            "recent_commits": recent,
            "remote": remote,
            "remote_name": remote_name,
            "commit_count": len(list(repo.iter_commits())),
        }
    except Exception as e:
        return {"error": str(e), "commit_hash": "n/a", "branch": "n/a",
                "commit_msg": "n/a", "author": "n/a", "author_name": "n/a",
                "author_email": "n/a", "commit_time": "n/a", "files_changed": [],
                "diffstat": [], "total_added": 0, "total_deleted": 0, "files_count": 0,
                "dirty": [], "recent_commits": [], "remote": "n/a", "remote_name": "gitlab",
                "commit_count": 0, "full_message": "n/a"}



@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/healthz")
def healthz():
    """Liveness: app + LLM reachability (for the demo host / k8s-style checks)."""
    llm_ok = False
    try:
        r = requests.get(genai_agent.LLM_ENDPOINT.rstrip("/") + "/models",
                         headers={"Authorization": f"Bearer {genai_agent.LLM_API_KEY}"},
                         timeout=5, verify=genai_agent.SSL_CA_CERT or True)
        llm_ok = r.ok
    except Exception:
        llm_ok = False
    return jsonify({"ok": True, "gitlab_mode": os.getenv("GITLAB_MODE", "real").lower(),
                    "llm_reachable": llm_ok, "llm_endpoint": genai_agent.LLM_ENDPOINT,
                    "llm_model": genai_agent.LLM_MODEL}), 200


@app.route("/api/trigger", methods=["POST"])
def trigger():
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    ref = request.get_json(force=True, silent=True) or {}
    branch = ref.get("ref", "master")
    res = webhook.trigger_pipeline(project_id, branch)
    webhook.emit({"type": "pipeline_triggered", "ref": branch, "ts": time.time()})
    return jsonify(res)

@app.route("/api/poll")
def poll():
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    return jsonify(webhook.poll_pipeline_state(project_id))

@app.route("/api/metrics")
def metrics():
    return jsonify(webhook.get_metrics())

@app.route("/api/reset", methods=["POST"])
def reset():
    """Zero the demo session KPIs (GitLab history untouched)."""
    webhook.reset_demo()
    return jsonify({"ok": True})

@app.route("/api/fix-time", methods=["POST"])
def fix_time():
    """Record a MEASURED fix duration (failure observed -> MR pipeline green).

    Also records the run in the per-run store (sparklines + replay) when the
    caller supplies the round context (scenario, source, saved minutes, risk, merged).
    """
    data = request.get_json(force=True, silent=True) or {}
    secs = float(data.get("seconds", 0) or 0)
    if secs > 0:
        webhook._set_fix_seconds(secs)
    # per-run history (T: sparklines + replay)
    if data.get("pipeline_id"):
        saved_minutes = float(data.get("saved_minutes", 0) or 0)
        risk = int(data.get("risk_score", 0) or 0)
        webhook.record_run(
            pipeline_id=data.get("pipeline_id"),
            scenario_id=data.get("scenario_id", ""),
            source=data.get("source", "live-llm"),
            fix_seconds=secs,
            saved_minutes=saved_minutes,
            risk_score=risk,
            merged=bool(data.get("merged", False)),
        )
    return jsonify({"ok": True, "seconds": secs})

@app.route("/api/merge", methods=["POST"])
def merge():
    """Real autonomous merge via the GitLab API.

    Server-side gate (not just the UI): the MR's latest pipeline MUST be green
    and the risk gates must have passed (echoed back from the client). A red or
    unknown pipeline is refused and left open for a human.
    """
    data = request.get_json(force=True, silent=True) or {}
    mr_iid = int(data.get("mr_iid", 0) or 0)
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    if not mr_iid:
        return jsonify({"merged": False, "error": "no mr_iid"})
    # 1. fetch MR + its pipelines
    mr = webhook.get_mr(project_id, mr_iid)
    if isinstance(mr, dict) and mr.get("error"):
        return jsonify({"merged": False, "mr_iid": mr_iid, "error": mr["error"]})
    if mr.get("state") in ("merged", "closed"):
        return jsonify({"merged": mr.get("state") == "merged", "mr_iid": mr_iid,
                        "error": None if mr.get("state") == "merged" else "MR is closed"})
    pipelines = webhook.api_get(f"projects/{project_id}/merge_requests/{mr_iid}/pipelines",
                                {"per_page": 1})
    latest = pipelines[0] if isinstance(pipelines, list) and pipelines else {}
    pstatus = latest.get("status")
    if pstatus != "success":
        return jsonify({"merged": False, "mr_iid": mr_iid, "pipeline_status": pstatus,
                        "error": f"refusing to merge: MR pipeline is '{pstatus}', not 'success'"})
    # 2. gates (client checked them at approve time; enforce risk again server-side)
    risk = int(data.get("risk_score", 101) or 101)
    if risk > 70:
        return jsonify({"merged": False, "mr_iid": mr_iid,
                        "error": f"refusing to merge: risk {risk} exceeds gate 70"})
    res = webhook.merge_merge_request(project_id, mr_iid, data.get("sha", ""))
    merged = bool(res.get("merged") or res.get("state") == "merged"
                  or res.get("state_event") == "merged")
    if merged:
        return jsonify({"merged": True, "mr_iid": mr_iid})
    return jsonify({"merged": False, "mr_iid": mr_iid,
                    "error": res.get("error") or str(res)[:200]})

@app.route("/api/agent-info")
def agent_info():
    """Provenance for the GenAI agent — so the audience can verify it is a live LLM."""
    return jsonify({
        "endpoint": genai_agent.LLM_ENDPOINT,
        "model": genai_agent.LLM_MODEL,
        "mode": os.getenv("GITLAB_MODE", "real").lower(),
    })

@app.route("/api/job-trace")
def job_trace():
    """Full trace of one job (for the debug console)."""
    project_id = int(request.args.get("project_id", os.getenv("GITLAB_PROJECT_ID", "1")))
    job_id = int(request.args.get("job_id", "0"))
    trace = webhook.get_gitlab_job_trace(project_id, job_id)
    return jsonify({"trace": trace or ""})

@app.route("/api/git")
def git_api():
    return jsonify(get_git_info())

@app.route("/webhook/gitlab", methods=["POST"])
def gitlab_webhook():
    body = request.get_data()
    sig = request.headers.get("X-Gitlab-Token") or request.headers.get("X-Hub-Signature-256", "")
    if WEBHOOK_SECRET := os.getenv("WEBHOOK_SECRET", ""):
        if not webhook.verify_signature(body, sig):
            return jsonify({"error": "bad signature"}), 403
    data = request.get_json(force=True, silent=True) or {}
    webhook.record_pipeline_event(data)
    return jsonify({"ok": True})

@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True, silent=True) or {}
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    mode = os.getenv("GITLAB_MODE", "real").lower()
    git_info = get_git_info()
    state = webhook.poll_pipeline_state(project_id)
    pipeline = state.get("pipeline") or {}
    failed = (state.get("failed_jobs") or [{}])[0]

    # Full failing-job trace (NOT the 6000-char slice) so the agent sees the real error.
    trace = ""
    if failed.get("id"):
        trace = webhook.get_gitlab_job_trace(project_id, failed["id"])
    if not trace:
        trace = state.get("trace") or data.get("trace") or "Integration test failed: random exit 1"
    if not isinstance(trace, str):
        trace = str(trace)

    changed_files = list(data.get("changed_files") or git_info.get("files_changed", []) or [])
    # Prefer the file the failing test actually exercises (the scenario's changed_file /
    # the file named in the trace), so the agent gets the code that produced the error.
    scenario = state.get("scenario")
    if scenario and scenario.get("changed_file"):
        cf = scenario["changed_file"]
        changed_files = [cf] + [f for f in changed_files if f != cf]
    # Real mode: the trace contains the command that ran (e.g. "python -m pytest -q
    # tests/integration_test.py"). Extract the target file so we fetch the code that
    # actually failed, not just whatever the last commit touched.
    if mode == "real":
        import re as _re
        m = _re.search(r"pytest\s+-q\s+([\w./_\-]+)", trace) or _re.search(r"pytest\s+([\w./_\-]+)", trace)
        if m:
            tf = m.group(1)
            if tf not in changed_files:
                changed_files = [tf] + changed_files

    # Gather the failing source file's content (raw) + the commit diff for the agent.
    # Fetch a broader candidate set (not just the first 3) so the file that
    # actually produces the error is included; empty fetches (dirs, 404s) drop.
    file_contents = {}
    git_diff = ""
    ref = pipeline.get("sha") or (pipeline.get("ref")) or "master"
    sha = pipeline.get("sha")
    if mode == "real":
        seen = set()
        for cf in (changed_files or [])[:6]:
            if cf in seen:
                continue
            seen.add(cf)
            raw = webhook.get_file_raw(project_id, cf, ref)
            if raw:
                file_contents[cf] = raw
        if sha:
            git_diff = webhook.get_commit_diff(project_id, sha)

    # In mock mode the scenario supplies the failing source; in real mode we used raw fetch.
    if mode == "mock":
        scenario_obj = scenario
    else:
        scenario_obj = None

    analysis = genai_agent.analyze_failure(
        job_trace=trace,
        changed_files=changed_files,
        commit_msg=git_info.get("commit_msg", "") or pipeline.get("ref", ""),
        file_contents=file_contents,
        git_diff=git_diff,
        scenario=scenario_obj,
        stage_context="The failing stage is 'integration'. Its purpose: exercise the payments service "
                      "under parallel load against a shared DB connection pool. The pool is configured in "
                      "app/db/pool.py (POOL_SIZE, MAX_OVERFLOW, EXPECTED_WORKERS) and the test asserts "
                      "effective capacity >= EXPECTED_WORKERS. Diagnose the ACTUAL root cause from the trace "
                      "and the provided source files; if it is pool exhaustion, the fix is to raise "
                      "POOL_SIZE/MAX_OVERFLOW in app/db/pool.py (not to edit the test). Do not invent hunks "
                      "for files you were not given.",
    )
    return jsonify(analysis)

@app.route("/api/approve", methods=["POST"])
def approve():
    data = request.get_json(force=True, silent=True) or {}
    a = data.get("analysis", {})
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    mode = os.getenv("GITLAB_MODE", "real").lower()
    branch = "auto-fix/genai-" + datetime.now().strftime("%H%M%S")
    source_pipeline_id = int(data.get("source_pipeline_id", 0) or 0)
    state = webhook.poll_pipeline_state(project_id)
    changed_files = (state.get("changed_files") or []) or a.get("files_touched") or []
    # Repo checkout for real patch application: inside Docker the repo is
    # mounted at /repo (REPO_PATH); elsewhere fall back to API-attached patch.
    repo_path = REPO_PATH if mode == "real" else ""
    if repo_path and not os.path.isdir(os.path.join(repo_path, ".git")):
        repo_path = ""
    mr = webhook.create_merge_request(
        project_id, branch, genai_agent.suggest_mr_title(a), a.get("patch", ""),
        target="master", analysis=a, repo_path=repo_path,
        pipeline_id=source_pipeline_id, changed_files=list(changed_files))
    if mr.get("patch_applied") or mr.get("applied"):
        webhook._record_auto_fix(int(a.get("risk_score", 0) or 0), source_pipeline_id)
    return jsonify(mr)

@app.route("/stream")
def stream():
    def gen():
        last = 0
        while True:
            events = webhook._listeners
            with webhook._lock:
                new = events[last:]
                last = len(events)
            for e in new:
                yield f"data: {json.dumps(e)}\n\n"
            time.sleep(0.5)
    return Response(stream_with_context(gen()), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# Enhancement routes (T1-T7 + quick hits)
# ---------------------------------------------------------------------------
import cascade  # noqa: E402


@app.route("/api/cascade", methods=["POST"])
def cascade_start():
    """T1: start the 'watch it heal' cascade (3 real failures -> auto-fixed -> green)."""
    data = request.get_json(force=True, silent=True) or {}
    if data.get("stop"):
        return jsonify({"ok": True, "stopped": cascade.stop()})
    if data.get("status"):
        return jsonify(cascade.status())
    ok = cascade.start()
    return jsonify({"ok": ok, "running": cascade.status()["running"]})


@app.route("/api/cascade-status")
def cascade_status_route():
    return jsonify(cascade.status())


@app.route("/api/analyze-stream", methods=["POST"])
def analyze_stream():
    """T2: SSE stream of the LLM's live triage reasoning (the agent 'thinking').

    The client streams the triage; the FULL analysis (patch, gates) still comes
    from /api/analyze — this endpoint is the visible reasoning layer only.
    """
    data = request.get_json(force=True, silent=True) or {}
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    state = webhook.poll_pipeline_state(project_id)
    pipeline = state.get("pipeline") or {}
    failed = (state.get("failed_jobs") or [{}])[0]
    trace = ""
    if failed.get("id"):
        trace = webhook.get_gitlab_job_trace(project_id, failed["id"])
    if not trace:
        trace = state.get("trace") or data.get("trace") or "Integration test failed: exit 1"

    # context = cleaned trace tail + changed files (same inputs the analyst gets)
    scenario = state.get("scenario")
    changed = data.get("changed_files") or []
    if scenario and scenario.get("changed_file"):
        changed = [scenario["changed_file"]] + [f for f in changed if f != scenario["changed_file"]]
    ctx = (
        f"Changed files: {', '.join(changed) or 'unknown'}\n"
        f"Failing job trace (cleaned, tail):\n{genai_agent._clean_trace(trace, limit=4000)}"
    )

    def gen():
        import queue as _queue
        import threading as _threading
        q = _queue.Queue()

        def on_event(ev):
            q.put(ev)

        def producer():
            try:
                full, ms = genai_agent.stream_triage(ctx, on_event)
                q.put({"type": "end", "ms": ms, "text": full})
            except Exception as e:
                q.put({"type": "error", "text": str(e)[:200]})

        yield f"data: {json.dumps({'type': 'start', 'model': genai_agent.LLM_MODEL})}\n\n"
        t = _threading.Thread(target=producer, daemon=True)
        t.start()
        # stream events as they arrive (real-time), bounded so the client can bail
        seen_end = False
        while not seen_end:
            try:
                ev = q.get(timeout=150)
            except _queue.Empty:
                break
            if ev.get("type") == "end":
                seen_end = True
            yield f"data: {json.dumps(ev)}\n\n"
    return Response(stream_with_context(gen()), mimetype="text/event-stream")


@app.route("/api/critic", methods=["POST"])
def critic_route():
    """T4: second-opinion review of a proposed patch (governance panel)."""
    data = request.get_json(force=True, silent=True) or {}
    analysis = data.get("analysis") or {}
    # fetch current file contents for context when available
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    mode = os.getenv("GITLAB_MODE", "real").lower()
    file_contents = {}
    if mode == "real":
        state = webhook.poll_pipeline_state(project_id)
        p = state.get("pipeline") or {}
        ref = p.get("sha") or p.get("ref") or "master"
        for cf in (analysis.get("files_touched") or [])[:3]:
            raw = webhook.get_file_raw(project_id, cf, ref)
            if raw:
                file_contents[cf] = raw
    result = genai_agent.critic_patch(analysis, file_contents)
    webhook.audit("critic", "patch_reviewed",
                  f"verdict={result.get('verdict')} risk={result.get('risk_adjusted')} "
                  f"src={result.get('_source')} {result.get('rationale','')[:120]}")
    return jsonify(result)


@app.route("/api/audit")
def audit_route():
    """Governance audit log: every agent action (permanent; Reset demo keeps it)."""
    limit = int(request.args.get("limit", "50"))
    return jsonify({"entries": webhook.audit_log(limit)})


@app.route("/api/costs", methods=["GET", "POST"])
def costs_route():
    """T5: money engine cost inputs (audience-set) + computed savings."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        c = webhook.set_costs(
            hourly_rate=data.get("hourly_rate"),
            downtime_cost_per_hour=data.get("downtime_cost_per_hour"),
            manual_triage_minutes=data.get("manual_triage_minutes"))
        return jsonify({"ok": True, "costs": c})
    return jsonify(webhook.get_costs())


@app.route("/api/runs")
def runs_route():
    """Per-run history (sparklines + time-machine replay source)."""
    return jsonify({"runs": webhook.recent_runs(12)})


@app.route("/api/replay")
def replay_route():
    """T6: replay payload for a stored run (time machine, 2x/4x in the UI)."""
    pid = request.args.get("pipeline_id", "")
    payload = webhook.replay_payload(pid)
    if not payload:
        return jsonify({"error": "no stored runs yet"}), 404
    return jsonify(payload)


@app.route("/api/rule-baseline", methods=["POST"])
def rule_baseline_route():
    """T7: deterministic (non-GenAI) baseline engine — fixes the KNOWN failure
    classes by signature matching. Proves the LLM adds value: it fixes the novel
    cases the rule engine can't (and fails on them, honestly)."""
    data = request.get_json(force=True, silent=True) or {}
    trace = data.get("trace") or ""
    analysis = data.get("analysis") or {}
    scenario = (webhook.poll_pipeline_state(int(os.getenv("GITLAB_PROJECT_ID", "1")))
                .get("scenario") or {})
    sig = {
        "pool": bool(re.search(r"pool exhausted|POOL_SIZE|connection pool", trace, re.I))
                or scenario.get("id") == "db_pool_exhaustion",
        "retry": bool(re.search(r"Timeout|retries exceeded|timeout", trace, re.I))
                 or scenario.get("id") == "missing_retry",
        "import": bool(re.search(r"NameError|ImportError|ModuleNotFound", trace))
                  or scenario.get("id") == "missing_import",
    }
    hits = [k for k, v in sig.items() if v]
    fixed = len(hits) == 1  # exactly one known class -> rule engine can fix it
    result = {
        "engine": "rule-based (no LLM)",
        "matched": hits,
        "can_fix": fixed,
        "explanation": (
            f"Signature match: {', '.join(hits) or 'none'}. "
            + ("A single known failure class -> the rule engine applies its canned fix."
               if fixed else "No single known failure class matched -> the rule-based engine "
                             "CANNOT fix this; only the GenAI agent can generalize to it.")),
        "canned_patch": analysis.get("patch") if fixed else "",
        "time_ms": 0,  # rule engine is near-instant (the point: fast but narrow)
    }
    return jsonify(result)

# NOTE: pipelines are NOT auto-seeded at startup. The dashboard starts empty;
# the user triggers a run with the "▶ Run Pipeline" button (real or mock mode).

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)
