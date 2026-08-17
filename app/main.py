import os
from flask import Flask, render_template_string, jsonify
from dotenv import load_dotenv
import git
import subprocess
from datetime import datetime

load_dotenv()

app = Flask(__name__)

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:18080/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-or-v1-default")
FLASK_PORT = int(os.getenv("FLASK_PORT", "8080"))

REPO_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def get_git_info():
    try:
        repo = git.Repo(REPO_PATH)
        commit = repo.head.commit
        commit_hash = commit.hexsha[:8]
        commit_msg = commit.message.split("\n")[0]
        commit_time = datetime.fromtimestamp(commit.committed_date).isoformat()
        author = f"{commit.author.name} <{commit.author.email}>"
        files_changed = [item.a_path for item in repo.index.diff(commit.parents[0] if commit.parents else None)]
        if not files_changed:
            # uncommitted changes
            files_changed = [item.a_path for item in repo.index.diff(None)]
        return {
            "branch": repo.active_branch.name,
            "commit_hash": commit_hash,
            "commit_msg": commit_msg,
            "commit_time": commit_time,
            "author": author,
            "files_changed": files_changed[:10]
        }
    except Exception as e:
        return {"error": str(e)}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CI/CD GenAI Demo</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#0b1220; --card:#111a2e; --muted:#8aa0c2; --accent:#4da3ff; --ok:#2ecc71; --warn:#f1c40f; --err:#e74c3c; --text:#e6eefc; }
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--text)}
header{padding:16px 24px;border-bottom:1px solid #1e2a45;display:flex;justify-content:space-between;align-items:center}
nav a{color:var(--muted);margin:0 12px;text-decoration:none}
.hero{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:24px}
.card{background:var(--card);border:1px solid #1e2a45;border-radius:12px;padding:16px}
.kpi{font-size:28px;font-weight:600}
.kpi-label{color:var(--muted);font-size:12px;margin-top:4px}
.main{display:grid;grid-template-columns:2fr 1fr;gap:16px;padding:0 24px 24px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.timeline{border-left:2px solid #1e2a45;padding-left:16px}
.stage{display:flex;gap:12px;margin:12px 0}
.dot{width:12px;height:12px;border-radius:50%;margin-top:4px}
.dot.ok{background:var(--ok)} .dot.err{background:var(--err)} .dot.warn{background:var(--warn)}
.badge{padding:2px 8px;border-radius:6px;font-size:11px;background:#1e2a45;color:var(--muted)}
.ai-summary{background:#0f1730;border:1px dashed #2a3a5e;border-radius:10px;padding:12px;margin:12px 0}
.actions{display:flex;gap:8px;margin-top:12px}
.btn{background:var(--accent);border:none;color:white;padding:8px 12px;border-radius:8px;cursor:pointer}
.btn.secondary{background:#1e2a45;color:var(--text)}
.config{padding:24px;color:var(--muted);font-size:13px}
.source-list{font-size:12px;color:var(--muted);margin-top:8px}
.source-item{padding:4px 0;border-bottom:1px solid #1e2a45}
.debug-console{margin-top:16px}
.debug-header{display:flex;justify-content:space-between;align-items:center}
.debug-content{max-height:200px;overflow:auto;background:#0a0f1a;border:1px solid #1e2a45;border-radius:8px;padding:8px;font-family:monospace;font-size:12px;color:#c5d0e0}
.collapsed .debug-content{display:none}
</style>
</head>
<body>
<header><div><strong>CI/CD GenAI</strong> <span style="color:var(--muted)">Demo</span></div><nav><a>Pipeline</a></nav></header>
<div class="hero">
  <div class="card"><div class="kpi">4 min</div><div class="kpi-label">MTTR • ↓ 82%</div></div>
  <div class="card"><div class="kpi">12</div><div class="kpi-label">Releases / week</div></div>
  <div class="card"><div class="kpi">87%</div><div class="kpi-label">Auto-Fix Rate</div></div>
  <div class="card"><div class="kpi">Low</div><div class="kpi-label">Risk Score</div></div>
</div>
<div class="main">
  <div class="card">
    <h3>Pipeline • main #1423</h3>
    <div class="timeline">
      <div class="stage"><div class="dot ok"></div><div><strong>Build</strong> Passed <span class="badge">OK</span></div></div>
      <div class="stage"><div class="dot ok"></div><div><strong>Unit Tests</strong> Passed <span class="badge">OK</span></div></div>
      <div class="stage"><div class="dot err"></div><div><strong>Integration Tests</strong> Failed</div></div>
    </div>
    <div class="ai-summary">
      <b>AI Root Cause:</b> Flaky DB pool exhaustion in payments.test<br>
      <b>Fix:</b> Increase pool max 10→20 + retry with backoff. Patch ready.
    </div>
    <div class="actions">
      <button class="btn">Approve Auto-Fix</button>
      <button class="btn secondary">View Patch</button>
    </div>
    <div class="debug-console" id="debug">
      <div class="debug-header">
        <strong>Debug Console</strong>
        <button class="btn secondary" onclick="toggleDebug()">Toggle</button>
      </div>
      <div class="debug-content">
        {{debug_log}}
      </div>
    </div>
  </div>
  <div class="card">
    <h3>Source & Commit</h3>
    <div class="config">
      Branch: {{git.branch}}<br>
      Commit: {{git.commit_hash}} — {{git.commit_msg}}<br>
      Author: {{git.author}}<br>
      Time: {{git.commit_time}}
      <div class="source-list">
        <strong>Changed files:</strong>
        {% for f in git.files_changed %}
        <div class="source-item">• {{f}}</div>
        {% endfor %}
      </div>
    </div>
    <h3 style="margin-top:16px">LLM Config</h3>
    <div class="config">Endpoint: {{endpoint}}<br>API Key: {{api_key_masked}}</div>
  </div>
</div>
<script>
function toggleDebug(){document.getElementById('debug').classList.toggle('collapsed')}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    masked = LLM_API_KEY[:4] + "*" * max(0, len(LLM_API_KEY)-8) + LLM_API_KEY[-4:] if len(LLM_API_KEY) > 8 else "****"
    git_info = get_git_info()
    debug_log = "2026-08-17 08:45:12 [INFO] Pipeline triggered by commit " + git_info.get("commit_hash","unknown") + "\n2026-08-17 08:45:30 [BUILD] docker build completed\n2026-08-17 08:46:12 [TEST] Integration tests failed: payments.test timeout\n2026-08-17 08:46:15 [AI] Root cause analysis started"
    return render_template_string(HTML_TEMPLATE, endpoint=LLM_ENDPOINT, api_key_masked=masked, git=git_info, debug_log=debug_log)

@app.route("/api/config")
def config():
    return jsonify({"llm_endpoint": LLM_ENDPOINT, "llm_api_key_set": bool(LLM_API_KEY)})

@app.route("/api/git")
def git_info_api():
    return jsonify(get_git_info())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT)
