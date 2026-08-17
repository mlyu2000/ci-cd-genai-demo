import os
import sys
import json
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, render_template_string, jsonify, request, Response, stream_with_context
from dotenv import load_dotenv
import git
import subprocess
from datetime import datetime

import webhook
import genai_agent

load_dotenv()

app = Flask(__name__)

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:18080/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-or-v1-default")
FLASK_PORT = int(os.getenv("FLASK_PORT", "8080"))

REPO_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
webhook.init_db()

def get_git_info():
    try:
        repo = git.Repo(REPO_PATH)
        commit = repo.head.commit
        files_changed = [i.a_path for i in repo.index.diff(commit.parents[0] if commit.parents else None)] or \
                       [i.a_path for i in repo.index.diff(None)]
        return {
            "branch": repo.active_branch.name,
            "commit_hash": commit.hexsha[:8],
            "commit_msg": commit.message.split("\n")[0],
            "commit_time": datetime.fromtimestamp(commit.committed_date).isoformat(),
            "author": f"{commit.author.name} <{commit.author.email}>",
            "files_changed": files_changed[:10],
        }
    except Exception as e:
        return {"error": str(e)}

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
.timeline{border-left:2px solid var(--border);padding-left:16px;margin-top:8px}
.stage{display:flex;gap:12px;margin:12px 0;align-items:center}
.dot{width:12px;height:12px;border-radius:50%;background:var(--muted)}
.dot.ok{background:var(--hpe-green)} .dot.err{background:var(--hpe-red)} .dot.run{background:var(--hpe-amber);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.badge{padding:2px 8px;border-radius:6px;font-size:11px;background:var(--border);color:var(--muted)}
.ai-card{background:linear-gradient(135deg,#10202e,#0c1a26);border:1px solid var(--hpe-teal);border-radius:12px;padding:16px;margin-top:14px}
.ai-card h4{margin:0 0 8px;color:var(--hpe-teal)}
.diff{background:#0a0f1a;border:1px solid var(--border);border-radius:8px;padding:10px;font-family:monospace;font-size:12px;white-space:pre-wrap;color:#c9d6e2;max-height:220px;overflow:auto}
.gauge{display:inline-block;width:46px;height:46px;border-radius:50%;background:conic-gradient(var(--c) calc(var(--v)*1%),var(--border) 0);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700}
.btn{background:var(--hpe-blue);border:none;color:#fff;padding:8px 14px;border-radius:8px;cursor:pointer;margin-right:8px}
.btn.secondary{background:var(--card);color:var(--text);border:1px solid var(--border)}
.source-list{font-size:12px;color:var(--muted);margin-top:8px}
.source-item{padding:4px 0;border-bottom:1px solid var(--border)}
.debug-console{margin-top:14px}
.debug-header{display:flex;justify-content:space-between;align-items:center}
.debug-content{max-height:180px;overflow:auto;background:#0a0f1a;border:1px solid var(--border);border-radius:8px;padding:8px;font-family:monospace;font-size:12px;color:#c5d0e0}
.collapsed .debug-content{display:none}
#stream{font-size:12px;color:var(--hpe-teal)}
</style>
</head>
<body>
<header>
  <div><strong style="color:var(--hpe-blue)">CI/CD</strong> <strong style="color:var(--hpe-teal)">GenAI</strong> <span style="color:var(--muted)">Demo</span></div>
  <div><nav><a>Pipeline</a><a>AI</a><a>Metrics</a></nav>
  <button class="theme-btn" onclick="toggleTheme()">Light/Dark</button></div>
</header>

<div class="hero">
  <div class="card"><div class="kpi" id="kpi-mttr">4m</div><div class="kpi-label">MTTR • ↓82%</div></div>
  <div class="card"><div class="kpi" id="kpi-rate">87%</div><div class="kpi-label">Auto-Fix Rate</div></div>
  <div class="card"><div class="kpi">12</div><div class="kpi-label">Releases / week</div></div>
  <div class="card"><div class="kpi" id="kpi-risk">Low</div><div class="kpi-label">Risk Score</div></div>
</div>

<div class="main">
  <div class="card">
    <h3>Pipeline • <span id="ref">main</span> #<span id="pid">1423</span></h3>
    <div class="timeline" id="timeline">
      <div class="stage"><div class="dot ok"></div><div><strong>Build</strong> Passed <span class="badge">OK</span></div></div>
      <div class="stage"><div class="dot ok"></div><div><strong>Unit Tests</strong> Passed <span class="badge">OK</span></div></div>
      <div class="stage"><div class="dot err" id="dot-int"></div><div><strong>Integration Tests</strong> <span id="int-status">Failed</span></div></div>
    </div>

    <div class="actions" style="margin-top:12px">
      <button class="btn" onclick="runPipeline()">▶ Run Pipeline</button>
      <button class="btn secondary" onclick="startPolling()">↻ Refresh</button>
    </div>

      <div class="ai-card" id="ai-card" style="display:none">
      <h4>🤖 AI Root-Cause & Auto-Fix</h4>
      <div id="ai-reason" style="white-space:pre-wrap"></div>
      <div style="margin:10px 0">
        <span class="gauge" id="g-conf" style="--c:var(--hpe-green);--v:0">0%</span>
        <span style="color:var(--muted);font-size:11px">Confidence</span>
        <span class="gauge" id="g-risk" style="--c:var(--hpe-amber);--v:0;margin-left:14px">0</span>
        <span style="color:var(--muted);font-size:11px">Risk</span>
      </div>
      <div class="diff" id="ai-diff"></div>
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
    <h3>Source & Commit</h3>
    <div style="font-size:13px;color:var(--muted)">
      Branch: <span id="g-branch"></span><br>
      Commit: <span id="g-hash"></span> — <span id="g-msg"></span><br>
      Author: <span id="g-author"></span><br>
      Time: <span id="g-time"></span>
      <div class="source-list" id="g-files"></div>
    </div>
    <h3 style="margin-top:16px">Live Event Stream</h3>
    <div id="stream"></div>
  </div>
</div>

<script>
let lastTrace="";
function toggleTheme(){const h=document.documentElement;h.dataset.theme=h.dataset.theme==='light'?'dark':'light';localStorage.setItem('theme',h.dataset.theme);}
const t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t;
function toggleDebug(){document.getElementById('debug').classList.toggle('collapsed');}
function log(msg){const d=document.getElementById('debug-log');d.textContent+=msg+"\n";d.scrollTop=d.scrollHeight;}
function streamEvent(e){const s=document.getElementById('stream');s.innerHTML+="<div>["+new Date().toLocaleTimeString()+"] "+JSON.stringify(e)+"</div>";}

// Git info
fetch('/api/git').then(r=>r.json()).then(g=>{
  document.getElementById('g-branch').textContent=g.branch||'-';
  document.getElementById('g-hash').textContent=g.commit_hash||'-';
  document.getElementById('g-msg').textContent=g.commit_msg||'-';
  document.getElementById('g-author').textContent=g.author||'-';
  document.getElementById('g-time').textContent=g.commit_time||'-';
  document.getElementById('g-files').innerHTML=(g.files_changed||[]).map(f=>'<div class="source-item">• '+f+'</div>').join('');
});
startPolling();

// Poll GitLab pipeline state (real, avoids webhook URL-blocker)
let pollTimer=null;
function pollState(){
  fetch('/api/poll').then(r=>r.json()).then(s=>{
    const p=s.pipeline||{};
    document.getElementById('ref').textContent=p.ref||'-';
    document.getElementById('pid').textContent=p.id||'-';
    // stage dots
    const js=s.jobs||[];
    const byName=n=>js.filter(j=>j.name===n)[0];
    const setDot=(id,st)=>{const e=document.getElementById(id); if(!e)return; e.className='dot '+(st==='success'?'ok':st==='failed'?'err':st==='running'?'run':'');};
    setDot('dot-build', (byName('build')||{}).status);
    setDot('dot-test', (byName('unit-test')||{}).status);
    const intj=byName('integration-test')||{};
    setDot('dot-int', intj.status);
    document.getElementById('int-status').textContent=intj.status?intj.status.toUpperCase():'-';
    if(intj.status==='failed'){ lastTrace=s.trace||''; runAI({name:'integration-test'}); }
    if(p.status==='success'){ document.getElementById('kpi-rate').textContent='100%'; }
    streamEvent({type:'poll',pipeline:p.status,jobs:js.length});
  });
}
function runPipeline(){
  fetch('/api/trigger',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ref:'master'})})
    .then(r=>r.json()).then(x=>{ log('Pipeline triggered: '+JSON.stringify(x).slice(0,200)); startPolling(); });
}
function startPolling(){ if(pollTimer)clearInterval(pollTimer); pollTimer=setInterval(pollState,4000); pollState(); }
function runAI(job){
  document.getElementById('ai-card').style.display='block';
  document.getElementById('ai-reason').textContent='Analyzing failure...';
  fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({trace:lastTrace,job:job.name||'integration-test'})})
   .then(r=>r.json()).then(a=>{
     document.getElementById('ai-reason').textContent='Root cause: '+a.root_cause+'\nSummary: '+a.summary;
     document.getElementById('g-conf').style.setProperty('--v',Math.round((a.confidence||0)*100));
     document.getElementById('g-conf').textContent=Math.round((a.confidence||0)*100)+'%';
     document.getElementById('g-risk').style.setProperty('--v',a.risk_score||0);
     document.getElementById('g-risk').textContent=a.risk_score||0;
     document.getElementById('ai-diff').textContent=a.patch||'(no diff)';
     window._analysis=a;
   });
}

function approveFix(){
  fetch('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({analysis:window._analysis})}).then(r=>r.json()).then(x=>{
    log('MR created: '+JSON.stringify(x)); window._mr=x;
  });
}
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
    """Trigger a new pipeline on the GitLab project (real)."""
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    ref = request.get_json(force=True, silent=True) or {}
    branch = ref.get("ref", "master")
    res = webhook.api_post(f"projects/{project_id}/pipeline", {"ref": branch})
    webhook.emit({"type": "pipeline_triggered", "ref": branch, "ts": time.time()})
    return jsonify(res)

@app.route("/api/poll")
def poll():
    """Poll latest GitLab pipeline + failed job trace."""
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    state = webhook.poll_pipeline_state(project_id)
    return jsonify(state)


@app.route("/api/git")
def git_api():
    return jsonify(get_git_info())

@app.route("/webhook/gitlab", methods=["POST"])
def gitlab_webhook():
    body = request.get_data()
    sig = request.headers.get("X-Gitlab-Token") or request.headers.get("X-Hub-Signature-256", "")
    if not webhook.verify_signature(body, sig):
        return jsonify({"error": "bad signature"}), 403
    data = request.get_json(force=True, silent=True) or {}
    webhook.record_pipeline_event(data)
    return jsonify({"ok": True})

@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True, silent=True) or {}
    git_info = get_git_info()
    analysis = genai_agent.analyze_failure(
        job_trace=data.get("trace", "Integration test failed: random exit 1"),
        changed_files=list(git_info.get("files_changed", []) or []),
        commit_msg=git_info.get("commit_msg", ""),
    )
    return jsonify(analysis)

@app.route("/api/approve", methods=["POST"])
def approve():
    data = request.get_json(force=True, silent=True) or {}
    a = data.get("analysis", {})
    project_id = int(os.getenv("GITLAB_PROJECT_ID", "1"))
    branch = "auto-fix/genai-" + datetime.now().strftime("%H%M%S")
    mr = webhook.create_merge_request(project_id, branch, genai_agent.suggest_mr_title(a), a.get("patch", ""))
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)
