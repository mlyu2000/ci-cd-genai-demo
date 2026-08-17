import os
from flask import Flask, render_template_string, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:18080/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-or-v1-default")
FLASK_PORT = int(os.getenv("FLASK_PORT", "8080"))

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
.timeline{border-left:2px solid #1e2a45;padding-left:16px}
.stage{display:flex;gap:12px;margin:12px 0}
.dot{width:12px;height:12px;border-radius:50%;margin-top:4px}
.dot.ok{background:var(--ok)} .dot.err{background:var(--err)}
.badge{padding:2px 8px;border-radius:6px;font-size:11px;background:#1e2a45;color:var(--muted)}
.ai-summary{background:#0f1730;border:1px dashed #2a3a5e;border-radius:10px;padding:12px;margin:12px 0}
.actions{display:flex;gap:8px;margin-top:12px}
.btn{background:var(--accent);border:none;color:white;padding:8px 12px;border-radius:8px;cursor:pointer}
.btn.secondary{background:#1e2a45;color:var(--text)}
.config{padding:24px;color:var(--muted);font-size:13px}
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
  </div>
  <div class="card">
    <h3>LLM Config</h3>
    <div class="config">Endpoint: {{endpoint}}<br>API Key: {{api_key_masked}}</div>
  </div>
</div>
</body>
</html>
"""

@app.route("/")
def index():
    masked = LLM_API_KEY[:4] + "*" * max(0, len(LLM_API_KEY)-8) + LLM_API_KEY[-4:] if len(LLM_API_KEY) > 8 else "****"
    return render_template_string(HTML_TEMPLATE, endpoint=LLM_ENDPOINT, api_key_masked=masked)

@app.route("/api/config")
def config():
    return jsonify({"llm_endpoint": LLM_ENDPOINT, "llm_api_key_set": bool(LLM_API_KEY)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT)
