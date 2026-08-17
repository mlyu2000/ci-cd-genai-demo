"""GenAI agent: analyze CI failures and synthesize an auto-fix patch via LiteLLM/CS1."""
import os
import json
import requests

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:18080/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-or-v1-default")
LLM_MODEL = os.getenv("LLM_MODEL", "muse-glimmer-30b")
SSL_CA_CERT = os.getenv("SSL_CA_CERT", "") or os.getenv("REQUESTS_CA_BUNDLE", "")

SYSTEM_PROMPT = (
    "You are a senior CI/CD reliability engineer and GenAI auto-fix agent. "
    "Given a failing pipeline job trace and the list of changed source files, "
    "identify the root cause, assess confidence and risk, and produce a minimal "
    "unified-diff patch that fixes the failure. Respond ONLY with strict JSON:\n"
    "{\n"
    '  "root_cause": "concise explanation",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "risk_score": 0-100,\n'
    '  "files_touched": ["path"],\n'
    '  "patch": "unified diff string or empty",\n'
    '  "summary": "one-line fix summary"\n'
    "}"
)

def analyze_failure(job_trace: str, changed_files: list[str], commit_msg: str = "") -> dict:
    """Call LiteLLM/CS1 and return parsed analysis dict. Falls back gracefully."""
    user_msg = (
        f"Commit message: {commit_msg}\n"
        f"Changed files: {', '.join(changed_files) or 'unknown'}\n\n"
        f"Failing job trace:\n{job_trace[-4000:]}"
    )
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.post(f"{LLM_ENDPOINT}/chat/completions", json=payload, headers=headers, timeout=120, verify=SSL_CA_CERT or True)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        return {
            "root_cause": f"LLM analysis unavailable: {e}",
            "confidence": 0.0,
            "risk_score": 50,
            "files_touched": [],
            "patch": "",
            "summary": "Fallback: manual review required",
        }

def suggest_mr_title(analysis: dict) -> str:
    return f"fix: {analysis.get('summary', 'auto-fix CI failure')[:60]}"
