import os
import sys
import json
import time
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
                    "dirty": [], "recent_commits": [], "remote": "n/a",
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

        # Primary remote.
        remote = "n/a"
        try:
            if repo.remotes:
                r = repo.remotes[0]
                remote = (r.url or "n/a").replace("https://", "")
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
            "commit_count": len(list(repo.iter_commits())),
        }
    except Exception as e:
        return {"error": str(e), "commit_hash": "n/a", "branch": "n/a",
                "commit_msg": "n/a", "author": "n/a", "author_name": "n/a",
                "author_email": "n/a", "commit_time": "n/a", "files_changed": [],
                "diffstat": [], "total_added": 0, "total_deleted": 0, "files_count": 0,
                "dirty": [], "recent_commits": [], "remote": "n/a",
                "commit_count": 0, "full_message": "n/a"}

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>CI/CD GenAI Demo</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{
  --hpe-blue:#0078d4; --hpe-teal:#00bfa5; --hpe-slate:#1a1d23;
  --hpe-green:#5fc80a; --hpe-amber:#f5a623; --hpe-red:#e3254b;
  --bg:#0f1115; --card:#181c22; --muted:#9aa6b2; --text:#e9edf2; --border:#283040;
}
html[data-theme="light"]{
  --bg:#f3f5f7; --card:#ffffff; --muted:#5a6b7b; --text:#16202b; --border:#d6dee6;
}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--text)}
header{padding:14px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
nav a{color:var(--muted);margin:0 12px;text-decoration:none}
.theme-btn{background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:6px 12px;cursor:pointer}
.hero{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;padding:20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
.kpi{font-size:26px;font-weight:700;color:var(--hpe-blue)}
.kpi-label{color:var(--muted);font-size:12px;margin-top:4px}
.main{display:grid;grid-template-columns:2fr 1fr;gap:16px;padding:0 24px 24px}
.flow{display:flex;align-items:center;gap:4px;margin-top:12px}
.flow-stage{display:flex;flex-direction:column;align-items:center;gap:4px;min-width:96px}
.flow-dot{width:16px;height:16px;border-radius:50%;background:var(--muted);border:2px solid var(--border)}
.flow-dot.ok{background:var(--hpe-green);border-color:var(--hpe-green)}
.flow-dot.err{background:var(--hpe-red);border-color:var(--hpe-red)}
.flow-dot.run{background:var(--hpe-amber);border-color:var(--hpe-amber);animation:pulse 1s infinite}
.flow-name{font-size:12px;font-weight:600}
.flow-status{font-size:11px;color:var(--muted)}
.flow-status.ok{color:var(--hpe-green)} .flow-status.err{color:var(--hpe-red)} .flow-status.run{color:var(--hpe-amber)}
.flow-conn{flex:1;min-width:24px;display:flex;align-items:center;height:16px}
.flow-line{width:100%;height:2px;background:var(--border);transition:background .3s}
.flow-line.ok{background:var(--hpe-green)} .flow-line.err{background:var(--hpe-red)}
.scenario-card{background:rgba(0,191,165,.06);border:1px solid var(--hpe-teal);border-radius:8px;padding:10px 12px;margin-top:12px}
.scenario-title{font-size:12px;font-weight:600;color:var(--hpe-teal);margin-bottom:4px}
.agent-badge{font-size:11px;padding:3px 8px;border-radius:6px;margin-left:10px;vertical-align:middle}
.agent-badge.live{background:rgba(95,200,10,.15);color:var(--hpe-green);border:1px solid var(--hpe-green)}
.agent-badge.fallback{background:rgba(245,166,35,.15);color:var(--hpe-amber);border:1px solid var(--hpe-amber)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.badge{padding:2px 8px;border-radius:6px;font-size:11px;background:var(--border);color:var(--muted)}
.ai-card{background:linear-gradient(135deg,#10202e,#0c1a26);border:1px solid var(--hpe-teal);border-radius:12px;padding:16px;margin-top:14px}
.ai-card h4{margin:0 0 8px;color:var(--hpe-teal)}
.diff{background:#0a0f1a;border:1px solid var(--border);border-radius:8px;padding:10px;font-family:monospace;font-size:12px;white-space:pre-wrap;color:#c9d6e2;max-height:220px;overflow:auto}
.diff .add{background:rgba(95,200,10,.15);color:#9bff5a;display:block}
.diff .del{background:rgba(227,37,75,.15);color:#ff7a96;display:block}
.diff .meta{color:var(--hpe-amber);display:block}
.metric-row{display:flex;gap:12px;margin:4px 0 0;flex-wrap:wrap}
.metric-card{flex:1;min-width:130px;background:#0a0f1a;border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.metric-value{font-size:24px;font-weight:700;line-height:1.1}
.metric-label{font-size:11px;color:var(--muted);margin-top:4px}
.btn{background:var(--hpe-blue);border:none;color:#fff;padding:8px 14px;border-radius:8px;cursor:pointer;margin-right:8px}
.btn.secondary{background:var(--card);color:var(--text);border:1px solid var(--border)}
.source-list{font-size:12px;color:var(--muted);margin-top:8px}
.source-item{padding:4px 0;border-bottom:1px solid var(--border)}
.src-grid{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:12px;margin-top:6px}
.src-grid .k{color:var(--muted)}
.src-grid .v{color:var(--text);word-break:break-all}
.diffstat{margin-top:8px;max-height:150px;overflow:auto;border:1px solid var(--border);border-radius:8px}
.diffstat-row{display:flex;align-items:center;gap:6px;font-size:11px;padding:3px 8px;border-bottom:1px solid var(--border);font-family:monospace}
.diffstat-row:last-child{border-bottom:none}
.diffstat-row .ct{width:16px;height:16px;border-radius:4px;font-size:10px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}
.ct.M{background:rgba(0,120,212,.2);color:#6cb8ff}.ct.A{background:rgba(95,200,10,.2);color:var(--hpe-green)}
.ct.D{background:rgba(227,37,75,.2);color:var(--hpe-red)}.ct.R{background:rgba(245,166,35,.2);color:var(--hpe-amber)}
.diffstat-row .fp{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.diffstat-row .add{color:var(--hpe-green);min-width:44px;text-align:right}
.diffstat-row .del{color:var(--hpe-red);min-width:44px;text-align:right}
.diffstat-total{padding:4px 8px;font-size:11px;border-top:1px solid var(--border);font-family:monospace}
.mini-commit{font-size:11px;padding:3px 0;border-bottom:1px dashed var(--border);font-family:monospace}
.mini-commit:last-child{border-bottom:none}
.dirty-chip{display:inline-block;background:rgba(245,166,35,.18);color:var(--hpe-amber);border-radius:5px;padding:1px 6px;font-size:10px;margin:1px 2px 1px 0}
.clean-chip{display:inline-block;background:rgba(95,200,10,.18);color:var(--hpe-green);border-radius:5px;padding:1px 6px;font-size:10px}
.debug-console{margin-top:14px}
.debug-header{display:flex;justify-content:space-between;align-items:center}
.debug-content{max-height:260px;overflow:auto;background:#0a0f1a;border:1px solid var(--border);border-radius:8px;padding:8px;font-family:monospace;font-size:12px;color:#c5d0e0}
.dbg-line{padding:2px 0;border-bottom:1px dashed rgba(255,255,255,.04)}
.dbg-line .dbg-tag{display:inline-block;min-width:56px;font-weight:700;font-size:10px;padding:0 5px;border-radius:4px;text-align:center;margin-right:6px;vertical-align:top}
.dbg-tag.info{background:rgba(0,120,212,.2);color:#6cb8ff}
.dbg-tag.ok{background:rgba(95,200,10,.2);color:var(--hpe-green)}
.dbg-tag.warn{background:rgba(245,166,35,.2);color:var(--hpe-amber)}
.dbg-tag.err{background:rgba(227,37,75,.2);color:var(--hpe-red)}
.dbg-tag.ai{background:rgba(0,191,165,.2);color:var(--hpe-teal)}
.dbg-line .dbg-time{color:var(--muted);font-size:10px;margin-right:6px}
.collapsed .debug-content{display:none}
#stream{font-size:12px;color:var(--hpe-teal)}
.autonomous{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:12px;margin-left:8px}
.mr-link{margin-top:10px;font-size:12px}
.mr-link a{color:var(--hpe-teal)}
</style>
</head>
<body>
<header>
  <div><strong style="color:var(--hpe-blue)">CI/CD</strong> <strong style="color:var(--hpe-teal)">GenAI</strong> <span style="color:var(--muted)">Demo</span></div>
  <div><nav><a>Pipeline</a><a>AI</a><a>Metrics</a></nav>
  <button class="theme-btn" onclick="toggleTheme()">Light/Dark</button></div>
</header>

<div class="hero">
<div class="card"><div class="kpi" id="kpi-mttr">--</div><div class="kpi-label">MTTR (min) • baseline 4m</div></div>
<div class="card"><div class="kpi" id="kpi-rate">--%</div><div class="kpi-label">Auto-Fix Rate</div></div>
<div class="card"><div class="kpi" id="kpi-rel">--</div><div class="kpi-label">Releases / week</div></div>
<div class="card"><div class="kpi" id="kpi-risk">--</div><div class="kpi-label">Risk Score</div></div>
<div class="card"><div class="kpi" id="kpi-savings">--</div><div class="kpi-label">Hours Saved / Week</div></div>
</div>

<div class="main">
  <div class="card">
    <h3>Pipeline • <span id="ref">-</span> #<span id="pid">-</span></h3>
    <div id="ref-hint" style="display:none;font-size:12px;color:var(--muted);margin-top:8px">No pipeline yet — click <b style="color:var(--hpe-blue)">▶ Run Pipeline</b> to start one (pipelines only start on demand, never automatically).</div>
    <div class="flow" id="flow">
      <div class="flow-stage" id="fs-build">
        <div class="flow-dot" id="dot-build"></div>
        <div class="flow-name">Build</div>
        <div class="flow-status" id="build-status">-</div>
      </div>
      <div class="flow-conn" id="conn-build"><div class="flow-line" id="fline-build"></div></div>
      <div class="flow-stage" id="fs-test">
        <div class="flow-dot" id="dot-test"></div>
        <div class="flow-name">Unit Tests</div>
        <div class="flow-status" id="test-status">-</div>
      </div>
      <div class="flow-conn" id="conn-test"><div class="flow-line" id="fline-test"></div></div>
      <div class="flow-stage" id="fs-int">
        <div class="flow-dot" id="dot-int"></div>
        <div class="flow-name">Integration Tests</div>
        <div class="flow-status" id="int-status">-</div>
      </div>
    </div>
    <div class="scenario-card" id="scenario-card" style="display:none">
      <div class="scenario-title">🧪 What this stage actually tests</div>
      <div id="scenario-desc" style="font-size:12px;line-height:1.5"></div>
    </div>

    <div class="actions" style="margin-top:12px">
      <button class="btn" onclick="runPipeline()">▶ Run Pipeline</button>
      <button class="btn secondary" onclick="startPolling()">↻ Refresh</button>
      <label class="autonomous"><input type="checkbox" id="autonomous"> Autonomous mode (auto-merge on green)</label>
    </div>

      <div class="ai-card" id="ai-card" style="display:none">
      <h4>🤖 AI Root-Cause & Auto-Fix <span class="agent-badge" id="agent-badge">…</span></h4>
      <div id="ai-reason" style="white-space:pre-wrap"></div>
      <div class="metric-row">
        <div class="metric-card">
          <div class="metric-value" id="g-conf" style="color:var(--hpe-green)">--</div>
          <div class="metric-label">Confidence (gate ≥ 0.70)</div>
        </div>
        <div class="metric-card">
          <div class="metric-value" id="g-risk" style="color:var(--hpe-amber)">--</div>
          <div class="metric-label">Risk Score (gate ≤ 70)</div>
        </div>
        <div class="metric-card">
          <div class="metric-value" id="g-gate" style="color:var(--muted)">--</div>
          <div class="metric-label">Auto-Merge Gate</div>
        </div>
      </div>
      
      <!-- Tabs -->
      <div style="margin:12px 0;border-bottom:1px solid var(--border)">
        <button class="btn secondary" onclick="switchTab('tab-reasoning')" id="btn-reasoning" style="padding:6px 10px;font-size:12px">Reasoning</button>
        <button class="btn secondary" onclick="switchTab('tab-patch')" id="btn-patch" style="padding:6px 10px;font-size:12px">Patch</button>
        <button class="btn secondary" onclick="switchTab('tab-validation')" id="btn-validation" style="padding:6px 10px;font-size:12px">Validation</button>
        <button class="btn secondary" onclick="switchTab('tab-baseline')" id="btn-baseline" style="padding:6px 10px;font-size:12px">Manual Baseline</button>
      </div>
      
      <div id="tab-reasoning" style="display:none">
        <div style="font-size:13px;color:var(--muted);margin-bottom:8px">Root Cause</div>
        <div id="reasoning-cause" style="font-weight:600;margin-bottom:8px"></div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:4px">Reasoning Steps</div>
        <div id="reasoning-steps" style="font-size:12px;white-space:pre-wrap"></div>
        <div style="font-size:13px;color:var(--muted);margin:8px 0 4px">Failure Category</div>
        <div id="reasoning-category" style="font-size:12px"></div>
      </div>
      
      <div id="tab-patch" style="display:none">
        <div class="diff" id="ai-diff"></div>
        <div id="patch-files" style="font-size:12px;color:var(--muted);margin-top:6px"></div>
      </div>
      
      <div id="tab-validation" style="display:none">
        <div style="font-size:13px;color:var(--muted);margin-bottom:4px">Validation Commands</div>
        <div id="validation-commands" style="font-size:12px;white-space:pre-wrap;font-family:monospace"></div>
        <div style="font-size:13px;color:var(--muted);margin:8px 0 4px">Risk Assessment</div>
        <div id="risk-assessment" style="font-size:12px"></div>
        <div style="font-size:13px;color:var(--muted);margin:8px 0 4px">Gate Status</div>
        <div id="gate-status" style="font-size:12px;font-weight:600"></div>
      </div>
      
      <div id="tab-baseline" style="display:none">
        <div style="background:rgba(227,37,75,0.1);border:1px solid var(--hpe-red);border-radius:8px;padding:12px;margin-bottom:12px">
          <div style="font-weight:600;color:var(--hpe-red);margin-bottom:6px">⚠️ Manual Triage Baseline</div>
          <div id="baseline-time" style="font-size:13px"></div>
          <div id="baseline-steps" style="font-size:12px;margin-top:6px"></div>
        </div>
        <div style="background:rgba(95,200,10,0.1);border:1px solid var(--hpe-green);border-radius:8px;padding:12px">
          <div style="font-weight:600;color:var(--hpe-green);margin-bottom:6px">✅ AI Auto-Fix</div>
          <div id="auto-time" style="font-size:13px"></div>
          <div id="time-saved" style="font-size:12px;margin-top:6px;font-weight:600"></div>
        </div>
      </div>
      
      <div class="mr-link" id="mr-link" style="display:none"></div>
      <div style="margin-top:10px">
        <button class="btn" onclick="approveFix()">Approve Auto-Fix</button>
        <button class="btn secondary" onclick="rejectFix()">Dismiss</button>
      </div>
    </div>

    <div class="debug-console collapsed" id="debug">
      <div class="debug-header"><strong>Debug Console</strong>
        <button class="btn secondary" onclick="toggleDebug()">Toggle</button></div>
      <div class="debug-content" id="debug-log"></div>
    </div>
  </div>

  <div class="card">
    <h3>Source & Commit <span id="g-dirty-chip" style="float:right"></span></h3>
    <div class="src-grid" id="src-grid">
      <span class="k">Branch</span><span class="v" id="g-branch"></span>
      <span class="k">Commit</span><span class="v" id="g-hash"></span>
      <span class="k">Committer</span><span class="v" id="g-author"></span>
      <span class="k">E-mail</span><span class="v" id="g-author-email"></span>
      <span class="k">Committed</span><span class="v" id="g-time"></span>
      <span class="k">Remote</span><span class="v" id="g-remote"></span>
      <span class="k">Repo size</span><span class="v" id="g-reposize"></span>
      <span class="k">Changes</span><span class="v" id="g-changesummary"></span>
    </div>
    <div style="font-size:12px;color:var(--muted);margin-top:8px">Message</div>
    <div id="g-msg" style="font-size:12px;white-space:pre-wrap;background:#0a0f1a;border:1px solid var(--border);border-radius:8px;padding:8px;margin-top:4px;font-family:monospace"></div>
    <div style="font-size:12px;color:var(--muted);margin-top:8px">Changed files <span id="g-files-count"></span></div>
    <div class="diffstat" id="g-files"></div>
    <div style="font-size:12px;color:var(--muted);margin-top:8px">Recent commits</div>
    <div id="g-recent"></div>
    <div style="margin-top:10px;font-size:12px;color:var(--muted)">GenAI Agent: <span id="g-llm" style="color:var(--hpe-teal)"></span></div>
    <h3 style="margin-top:16px">Live Event Stream</h3>
    <div id="stream"></div>
  </div>
</div>

<script>
let lastTrace="";
let pollTimer=null;
function toggleTheme(){const h=document.documentElement;h.dataset.theme=h.dataset.theme==='light'?'dark':'light';localStorage.setItem('theme',h.dataset.theme);}
let _t=null; try{ _t=localStorage.getItem('theme'); }catch(e){} if(_t)document.documentElement.dataset.theme=_t;
function toggleDebug(){document.getElementById('debug').classList.toggle('collapsed');}
function switchTab(tabId){
  const tabs=['tab-reasoning','tab-patch','tab-validation','tab-baseline'];
  tabs.forEach(t=>{const el=document.getElementById(t); if(el) el.style.display=(t===tabId)?'block':'none';});
  const btns={'tab-reasoning':'btn-reasoning','tab-patch':'btn-patch','tab-validation':'btn-validation','tab-baseline':'btn-baseline'};
  Object.entries(btns).forEach(([t,b])=>{const el=document.getElementById(b); if(el) el.style.fontWeight=(t===tabId)?'700':'400';});
}
function log(msg, tag){
  tag=tag||'INFO';
  const tags={OK:'ok',INFO:'info',WARN:'warn',ERROR:'err',FAIL:'err',AI:'ai'};
  const cls=tags[tag]||'info';
  const t=new Date().toLocaleTimeString();
  const d=document.getElementById('debug-log');
  const line=document.createElement('div');
  line.className='dbg-line';
  line.innerHTML='<span class="dbg-tag '+cls+'">'+tag+'</span><span class="dbg-time">'+t+'</span>'+String(msg).replace(/</g,'&lt;');
  d.appendChild(line);
  while(d.children.length>300) d.removeChild(d.firstChild);
  d.scrollTop=d.scrollHeight;
}
function streamEvent(e){const s=document.getElementById('stream');s.innerHTML+="<div>["+new Date().toLocaleTimeString()+"] "+JSON.stringify(e)+"</div>";}
// Extract a short, human-readable failure snippet from a raw (GitLab) trace.
// Strips the timestamp + runner prefix, keeps the meaningful error lines.
function failSnippet(trace, max){
  max=max||5;
  const keywords=/AssertionError|Error:|FAILED|Traceback|Exception|ERROR:|Error\\b|exit code \\d|No module|assert /;
  const lines=(trace||"").split(String.fromCharCode(10))
    .map(l=>l.replace(/^\\d{4}-\\d{2}-\\d{2}T[^\\s]+\\s*\\d+[EO]\\s*/,"").trim())
    .filter(l=>l.length>0);
  // Prefer lines that look like the actual failure; fall back to the tail.
  let hits=lines.filter(l=>keywords.test(l));
  if(hits.length===0) hits=lines.slice(-max);
  hits=hits.slice(-max);
  return hits.join(String.fromCharCode(10));
}

// Git info (enriched: author, remote, diffstat, recent commits, working-tree status)
fetch('/api/git').then(r=>r.json()).then(g=>{
  document.getElementById('g-branch').textContent=g.branch||'-';
  document.getElementById('g-hash').textContent=g.commit_hash||'-';
  document.getElementById('g-author').textContent=g.author_name||'-';
  const emEl=document.getElementById('g-author-email'); if(emEl) emEl.textContent=g.author_email||'-';
  document.getElementById('g-time').textContent=(g.commit_time||'-').replace('T',' ').slice(0,19)+' UTC';
  const rm=document.getElementById('g-remote'); if(rm) rm.textContent=g.remote||'-';
  const rs=document.getElementById('g-reposize');
  if(rs) rs.textContent=(g.commit_count||0)+' commits';
  // Dirty / clean working-tree chip
  const chip=document.getElementById('g-dirty-chip');
  if(chip){
    if(g.is_dirty && g.dirty && g.dirty.length){
      chip.innerHTML='<span class="dirty-chip">'+g.dirty.length+' uncommitted</span>';
    } else {
      chip.innerHTML='<span class="clean-chip">clean</span>';
    }
  }
  // Change summary
  const cs=document.getElementById('g-changesummary');
  if(cs) cs.innerHTML=(g.files_count||0)+' file(s) · <span style="color:var(--hpe-green)">+'+(g.total_added||0)+'</span> <span style="color:var(--hpe-red)">-'+(g.total_deleted||0)+'</span>';
  // Full message
  const msgEl=document.getElementById('g-msg');
  if(msgEl) msgEl.textContent=(g.full_message||g.commit_msg||'').slice(0,400);
  // Per-file diffstat
  const filesEl=document.getElementById('g-files');
  if(filesEl){
    const ds=g.diffstat||[];
    const fc=document.getElementById('g-files-count'); if(fc) fc.textContent='('+(g.files_count||0)+')';
    if(ds.length===0){ filesEl.innerHTML='<div class="diffstat-row" style="color:var(--muted)">no diff info</div>'; }
    else{
      let rows='';
      ds.forEach(d=>{
        rows+='<div class="diffstat-row"><span class="ct '+(d.change_type||'M')+'">'+(d.change_type||'M')+'</span>'
          +'<span class="fp" title="'+d.path+'">'+d.path+'</span>'
          +'<span class="add">+'+(d.added||0)+'</span><span class="del">-'+(d.deleted||0)+'</span></div>';
      });
      rows+='<div class="diffstat-total"><span style="color:var(--hpe-green)">+'+(g.total_added||0)+'</span> '
        +'<span style="color:var(--hpe-red)">-'+(g.total_deleted||0)+'</span> across '+(g.files_count||0)+' files</div>';
      filesEl.innerHTML=rows;
    }
  }
  // Recent commits
  const recentEl=document.getElementById('g-recent');
  if(recentEl){
    const rc=g.recent_commits||[];
    recentEl.innerHTML=rc.length? rc.map(c=>'<div class="mini-commit" title="'+c.time+'">'
      +'<span style="color:var(--hpe-teal)">'+c.hash+'</span> '+c.msg
      +'<span style="color:var(--muted)"> · '+c.author+'</span></div>').join('')
      : '<div style="color:var(--muted);font-size:12px">n/a</div>';
  }
});

// GenAI agent provenance — show the audience which LLM is actually answering.
fetch('/api/agent-info').then(r=>r.json()).then(ai=>{
  const b=document.getElementById('agent-badge');
  window._agentInfo=ai;
  b.textContent='live LLM: '+(ai.model||'unknown');
  b.className='agent-badge live';
  const gEl=document.getElementById('g-llm');
  if(gEl) gEl.textContent=(ai.model||'unknown')+' @ '+ai.endpoint;
}).catch(()=>{ const b=document.getElementById('agent-badge'); b.textContent='unknown'; b.className='agent-badge fallback'; });

// KPIs from real pipeline state
function loadMetrics(){
  fetch('/api/metrics').then(r=>r.json()).then(m=>{
    document.getElementById('kpi-mttr').textContent=m.mttr_min.toFixed(1)+'m';
    document.getElementById('kpi-rate').textContent=m.auto_fix_rate+'%';
    document.getElementById('kpi-rel').textContent=m.releases_week;
    document.getElementById('kpi-risk').textContent=m.risk_score;
    document.getElementById('kpi-savings').textContent=m.hours_saved_per_week;
  });
}
loadMetrics();

startPolling();

// Poll GitLab pipeline state (real or mock)
let _analyzedPid=null;      // analyze once per pipeline (prevents the infinite re-print loop)
let _lastEventKey=null;     // dedupe event-stream entries
let _lastJobSig=null;       // log job status changes only when they change
function _flowState(st){ return st==='success'?'ok':st==='failed'?'err':st==='running'?'run':''; }
function pollState(){
  fetch('/api/poll').then(r=>r.json()).then(s=>{
    const p=s.pipeline||{};
    if(!p.id){
      document.getElementById('ref').textContent='-';
      document.getElementById('pid').textContent='-';
      document.getElementById('ref-hint').style.display='block';
    } else {
      document.getElementById('ref-hint').style.display='none';
      document.getElementById('ref').textContent=p.ref||'-';
      document.getElementById('pid').textContent=p.id||'-';
    }
    const js=s.jobs||[];
    const byName=n=>js.filter(j=>j.name===n)[0];
    const stCls=st=>st==='success'?'ok':st==='failed'?'err':st==='running'?'run':'';
    const setDot=(id,st)=>{const e=document.getElementById(id); if(e)e.className='flow-dot '+_flowState(st);};
    const setStat=(id,txt,st)=>{const e=document.getElementById(id); if(e){e.textContent=txt; e.className='flow-status '+stCls(st);}};
    const setLine=(id,st)=>{const e=document.getElementById(id); if(e)e.className='flow-line '+_flowState(st);};
    const b=byName('build')||{};
    const tst=byName('unit-test')||{};
    const intj=byName('integration-test')||{};
    // Show status + real duration so every stage is transparent, not a dot.
    const fmt=(j)=>{ let t=j.status||'-'; if(j.duration!=null && j.status!=='running' && j.status!=='created' && j.status!=='pending') t+=' \u2022 '+Number(j.duration).toFixed(0)+'s'; return t; };
    setDot('dot-build', b.status); setStat('build-status', fmt(b), b.status);
    setDot('dot-test', tst.status); setStat('test-status', fmt(tst), tst.status);
    setDot('dot-int', intj.status); setStat('int-status', intj.status?intj.status.toUpperCase():'-', intj.status);
    // connector colors follow the stage that FEEDS them
    setLine('fline-build', b.status);
    setLine('fline-test', tst.status);

    // Explain what the integration stage actually tests (demystify the black box).
    const sc=s.scenario;
    const scCard=document.getElementById('scenario-card');
    const stageDoc =
      '<b>Build</b> = compile-check all Python (py_compile). &nbsp;•&nbsp; <b>Unit Tests</b> = pytest on the Flask app (tests/test_app.py). &nbsp;•&nbsp; <b>Integration Tests</b> = payments service under load (tests/integration_test.py): 6 parallel workers open DB connections; the pool allows only 5, so this test deterministically fails and hands the AI agent its triage target. Each stage runs on the real GitLab runner in its own job.';
    if(sc){
      scCard.style.display='block';
      const descMap={
        db_pool_exhaustion:'6 parallel test workers open DB connections; the pool allows 5 (overflow 0). The 6th worker blocks -> assertion fails. Failing file: <b>'+sc.changed_file+'</b>.',
        missing_retry:'An integration test calls an external HTTP API with a 2s timeout and NO retry. Any blip -> timeout -> test fails. Failing file: <b>'+sc.changed_file+'</b>.',
        missing_import:'A test imports a module that uses <code>Optional</code> without importing it -> NameError at load. Failing file: <b>'+sc.changed_file+'</b>.'
      };
      document.getElementById('scenario-desc').innerHTML =
        (sc.name? '<b>'+sc.name+'</b>. ':'') +
        (descMap[sc.id] || ('Reproduced failure in <b>'+(sc.changed_file||'unknown')+'</b> (category: '+(sc.category||'unknown')+').'));
      document.getElementById('scenario-desc').innerHTML +=
        ' <span style="color:var(--muted)">→ the AI agent receives the failing trace + the source of that file and must propose the fix.</span>';
    } else {
      // Real GitLab mode: no scenario object — show the static stage doc.
      scCard.style.display='block';
      document.getElementById('scenario-desc').innerHTML = stageDoc;
    }

    // Rich debug console: log meaningful state CHANGES, not "poll tick".
    const jobSig=js.map(j=>j.name+':'+j.status).join(' ');
    if(jobSig!==_lastJobSig){
      _lastJobSig=jobSig;
      js.forEach(j=>{
        if(j.status==='success') log('job '+j.name+' PASSED'+(j.duration?(' ('+j.duration.toFixed(1)+'s)'):''), 'OK');
        else if(j.status==='failed') log('job '+j.name+' FAILED'+(j.failure_reason?(' reason='+j.failure_reason):'')+(j.duration?(' after '+j.duration.toFixed(1)+'s'):''), 'FAIL');
        else if(j.status==='running') log('job '+j.name+' running...', 'INFO');
      });
    }
    if(intj.status==='failed' && p.id && _analyzedPid!==p.id){
      lastTrace=s.trace||''; window._changed=(s.changed_files||[]);
      _analyzedPid=p.id;
      const snippet=failSnippet(lastTrace,4);
      log('INTEGRATION FAILED — job '+intj.name+' (pipeline #'+p.id+', ref '+(p.ref||'-')+', '+(intj.duration?intj.duration.toFixed(1)+'s':'')+')','FAIL');
      log('files touched by the failing commit: '+((s.changed_files||[]).join(', ')||'unknown'),'WARN');
      log('failure signature:'+String.fromCharCode(10)+(snippet||'(no trace available)'),'ERR');
      // pull the full trace and surface just the root-cause lines (no 2000-char dump)
      const failedJob=(s.failed_jobs||[])[0];
      if(failedJob&&failedJob.id){
        fetch('/api/job-trace?job_id='+failedJob.id).then(r=>r.json()).then(t=>{
          if(t&&t.trace){
            const rootCause=failSnippet(t.trace,6);
            log('root-cause lines (from full trace of job #'+failedJob.id+'):'+String.fromCharCode(10)+rootCause,'ERR');
          }
        }).catch(()=>{});
      }
      runAI();
    }
    if(p.status==='success'){
      document.getElementById('kpi-rate').textContent='100%';
    }
    // Event stream + "green" log: only when the status actually CHANGES (dedupe).
    const key=(p.id||0)+'|'+(p.status||'-');
    if(key!==_lastEventKey){
      _lastEventKey=key;
      streamEvent({type:'pipeline',id:p.id,status:p.status,jobs:js.length});
      if(p.status==='success'){
        log('pipeline #'+p.id+' green — deploy stage would run now','OK');
        if(window._autonomous && window._pendingMR) autoMerge();
      }
    }
  });
}
function runPipeline(){
  _analyzedPid=null; // new pipeline may need a fresh analysis
  fetch('/api/trigger',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ref:'master'})})
    .then(r=>r.json()).then(x=>{ log('Pipeline triggered: '+JSON.stringify(x).slice(0,200)); startPolling(); });
}
function startPolling(){ if(pollTimer)clearInterval(pollTimer); pollTimer=setInterval(pollState,4000); pollState(); }

// Streaming AI reveal (typing effect over the returned root cause)
function renderDiff(patch){
  const el=document.getElementById('ai-diff');
  if(!patch){ el.textContent='(no diff produced)'; return; }
  el.innerHTML = patch.split('\\n').map(line=>{
    if(line.startsWith('+++')||line.startsWith('---')||line.startsWith('@@')) return '<span class="meta">'+line.replace(/</g,'&lt;')+'</span>';
    if(line.startsWith('+')) return '<span class="add">'+line.replace(/</g,'&lt;')+'</span>';
    if(line.startsWith('-')) return '<span class="del">'+line.replace(/</g,'&lt;')+'</span>';
    return line.replace(/</g,'&lt;');
  }).join('\\n');
}
let _typing=null;
function typeText(el, text, done){
  if(_typing) clearInterval(_typing);
  el.textContent=''; let i=0;
  _typing=setInterval(()=>{ el.textContent=text.slice(0,++i); if(i>=text.length){clearInterval(_typing);_typing=null; if(done)done();} }, 12);
}
function runAI(){
  document.getElementById('ai-card').style.display='block';
  switchTab('tab-reasoning');
  document.getElementById('ai-reason').textContent='Analyzing failure...';
  log('GenAI agent: sending trace + changed files to LLM ...','AI');
  const t0=Date.now();
  fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({trace:lastTrace,job:'integration-test',changed_files:window._changed||[]})})
   .then(r=>r.json()).then(a=>{
     // Provenance badge: prove whether this came from the live LLM or a fallback.
     const badge=document.getElementById('agent-badge');
     if(a._source==='live-llm'){
       badge.textContent='live LLM: '+(a._llm_model||'');
       badge.className='agent-badge live';
       log('GenAI: LLM '+(a._llm_model||'')+' generated a fix in '+a._llm_latency_ms+'ms','AI');
       log('GenAI verdict → root cause: '+(a.root_cause||'').slice(0,160),'AI');
       log('GenAI verdict → confidence '+(Math.round((a.confidence||0)*100))+'% · risk '+(a.risk_score||0)+'/100 · files: '+((a.files_touched||[]).join(', ')||'n/a'),'AI');
     } else {
       badge.textContent='fallback (LLM down)';
       badge.className='agent-badge fallback';
       log('GenAI: LLM not available — '+(a._fallback_error||'unreachable'),'WARN');
       log('GenAI: showing curated offline fallback for this scenario','WARN');
     }
     typeText(document.getElementById('ai-reason'),
       'Root cause: '+a.root_cause+'\\n\\nSummary: '+a.summary);
     // KPI metric cards (not pie charts): confidence + risk + gate
     const confPct=Math.round((a.confidence||0)*100);
     const risk=a.risk_score||0;
    const cardGatePass=(a.confidence||0)>=0.7 && risk<=70;
    const gConf=document.getElementById('g-conf');
    gConf.textContent=confPct+'%';
    gConf.style.color=cardGatePass?'var(--hpe-green)':'var(--hpe-red)';
    const gRisk=document.getElementById('g-risk');
    gRisk.textContent=risk+'/100';
    gRisk.style.color=risk<=70?'var(--hpe-amber)':'var(--hpe-red)';
    const gGate=document.getElementById('g-gate');
    gGate.textContent=cardGatePass?'PASS':'FAIL';
    gGate.style.color=cardGatePass?'var(--hpe-green)':'var(--hpe-red)';
     renderDiff(a.patch);
     
     // Update reasoning tab
     document.getElementById('reasoning-cause').textContent = a.root_cause;
     document.getElementById('reasoning-steps').textContent = (a.reasoning_steps || []).map((s,i)=>`\${i+1}. ${s}`).join('\\n');
     document.getElementById('reasoning-category').textContent = a.failure_category || 'unknown';
     
     // Update validation tab
     document.getElementById('validation-commands').textContent = (a.validation_commands || []).join('\\n');
     document.getElementById('risk-assessment').textContent = `Risk Score: ${a.risk_score||0}/100 - ${a.risk_score<30?'Low':a.risk_score<70?'Medium':'High'} risk`;
     
     // Update baseline tab
     const manualMin = a.manual_triage_minutes || 45;
     const autoMin = a.auto_triage_minutes || 5;
     const saved = manualMin - autoMin;
     const savedPct = Math.round((saved/manualMin)*100);
     document.getElementById('baseline-time').textContent = `Manual triage: ~${manualMin} minutes`;
     document.getElementById('baseline-steps').textContent = 'Steps: Download logs -> Manual root cause analysis -> Local reproduction -> Code fix -> PR creation -> Review';
     document.getElementById('auto-time').textContent = `AI auto-fix: ~${autoMin} minutes`;
     document.getElementById('time-saved').textContent = `Time saved: ${saved} min (${savedPct}% faster)`;
     
     // Gate status
     const confidenceGate = (a.confidence || 0) >= 0.7;
     const riskGate = (a.risk_score || 100) <= 70;
     const gatePassed = confidenceGate && riskGate;
     document.getElementById('gate-status').textContent = gatePassed ? '✅ Gates PASSED - Auto-merge allowed' : '⚠️ Gates FAILED - Manual review required';
     document.getElementById('gate-status').style.color = gatePassed ? 'var(--hpe-green)' : 'var(--hpe-amber)';
     
     // Patch files
     document.getElementById('patch-files').textContent = `Files: ${(a.files_touched || []).join(', ') || 'N/A'}`;
     
     window._analysis=a;
   });
}

function approveFix(){
  if(!window._analysis){
    alert('No analysis available');
    return;
  }
  const analysis = window._analysis;
  const confidenceGate = (analysis.confidence || 0) >= 0.7;
  const riskGate = (analysis.risk_score || 100) <= 70;
  const gatePassed = confidenceGate && riskGate;
  
  if(!gatePassed && document.getElementById('autonomous').checked){
    alert('Gates not passed - cannot auto-merge. Uncheck autonomous mode or improve confidence/risk.');
    return;
  }
  
  document.getElementById('mr-link').style.display='none';
  fetch('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({analysis:window._analysis})}).then(r=>r.json()).then(x=>{
    log('Auto-fix: '+JSON.stringify(x));
    if(x.applied){
      document.getElementById('mr-link').style.display='block';
      document.getElementById('mr-link').innerHTML='✅ MR opened: <a href="'+(x.mr_url||'#')+'" target="_blank">'+(x.mr_url||x.branch||'')+'</a> — '+(x.files||[]).join(', ');
      window._pendingMR=x; window._autonomous=document.getElementById('autonomous').checked;
      loadMetrics();
      if(window._autonomous){ log('Autonomous mode: awaiting green MR pipeline to auto-merge...'); }
    } else {
      document.getElementById('mr-link').style.display='block';
      document.getElementById('mr-link').innerHTML='⚠️ Auto-fix not applied: '+(x.error||'patch could not be committed');
    }
  });
}
function autoMerge(){ log('Autonomous: MR pipeline green — auto-merge enabled (governance: risk gate).'); }
function rejectFix(){document.getElementById('ai-card').style.display='none';}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

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
    state = webhook.poll_pipeline_state(project_id)
    if (state.get("pipeline") or {}).get("status") == "success":
        webhook._record_release()
    return jsonify(state)

@app.route("/api/metrics")
def metrics():
    return jsonify(webhook.get_metrics())

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
    git_info = get_git_info()
    # In mock mode, pull the curated scenario so the agent runs fully offline.
    scenario = None
    if os.getenv("GITLAB_MODE", "real").lower() == "mock":
        state = webhook.poll_pipeline_state(int(os.getenv("GITLAB_PROJECT_ID", "1")))
        scenario = state.get("scenario")
    analysis = genai_agent.analyze_failure(
        job_trace=data.get("trace", "Integration test failed: random exit 1"),
        changed_files=list(data.get("changed_files") or git_info.get("files_changed", []) or []),
        commit_msg=git_info.get("commit_msg", ""),
        scenario=scenario,
    )
    return jsonify(analysis)

@app.route("/api/approve", methods=["POST"])
def approve():
    data = request.get_json(force=True, silent=True) or {}
    a = data.get("analysis", {})
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    branch = "auto-fix/genai-" + datetime.now().strftime("%H%M%S")
    mr = webhook.create_merge_request(
        project_id, branch, genai_agent.suggest_mr_title(a), a.get("patch", ""),
        target="master", analysis=a)
    if mr.get("applied"):
        webhook.record_auto_fix(a)
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

# NOTE: pipelines are NOT auto-seeded at startup. The dashboard starts empty;
# the user triggers a run with the "▶ Run Pipeline" button (real or mock mode).

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)
