"""GenAI agent: analyze CI failures and synthesize an auto-fix patch via LiteLLM/CS1."""

import os
import json
import requests

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:9000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5-4b")
SSL_CA_CERT = os.getenv("SSL_CA_CERT", "") or os.getenv("REQUESTS_CA_BUNDLE", "")

SYSTEM_PROMPT = """You are a senior CI/CD reliability engineer and GenAI auto-fix agent.

Given a failing pipeline job trace, changed files, file contents, and git diff, identify root cause, assess confidence and risk, and produce a minimal unified-diff patch.

Respond ONLY with strict JSON:
{
  "root_cause": "concise explanation",
  "reasoning_steps": ["step 1", "step 2", ...],
  "confidence": 0.0-1.0,
  "risk_score": 0-100,
  "files_touched": ["path"],
  "patch": "unified diff string or empty",
  "summary": "one-line fix summary",
  "validation_commands": ["command1", "command2"],
  "failure_category": "resource|reliability|code|config",
  "manual_triage_minutes": 45,
  "auto_triage_minutes": 2
}
"""

def analyze_failure(job_trace: str, changed_files: list[str], commit_msg: str = "", file_contents: dict = None, git_diff: str = "", scenario: dict = None) -> dict:
    """Generate an analysis dict for the failure.

    If a scenario dict is provided (mock mode demo), synthesize the analysis
    from the curated scenario data so the demo runs fully offline. Otherwise
    call LiteLLM/CS1 (or fall back gracefully if unreachable).
    """
    # --- Offline / mock path: use curated scenario data ---
    if scenario:
        return {
            "root_cause": scenario.get("root_cause", "Unknown"),
            "reasoning_steps": scenario.get("reasoning_steps", []),
            "confidence": scenario.get("confidence", 0.9),
            "risk_score": scenario.get("risk_score", 30),
            "files_touched": [scenario.get("changed_file", "")] if scenario.get("changed_file") else changed_files,
            "patch": scenario.get("git_diff", ""),
            "summary": scenario.get("root_cause", "auto-fix")[:60],
            "validation_commands": scenario.get("validation_commands", []),
            "failure_category": scenario.get("category", "unknown"),
            "manual_triage_minutes": scenario.get("manual_triage_minutes", 45),
            "auto_triage_minutes": scenario.get("auto_triage_minutes", 5),
        }

    # --- Live LLM path (local qwen / vLLM) ---
    file_context = ""
    if file_contents:
        for path, content in file_contents.items():
            file_context += f"\n--- {path} ---\n{content}\n"
    
    user_msg = (
        f"Commit message: {commit_msg}\n"
        f"Changed files: {', '.join(changed_files) or 'unknown'}\n"
        f"Git diff:\n{git_diff[:2000]}\n"
        f"File contents:{file_context}\n\n"
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
        # Strip any <think>...</think> reasoning blocks qwen may emit, then parse JSON.
        import re as _re
        content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL).strip()
        result = json.loads(content)
        result.setdefault("reasoning_steps", [])
        result.setdefault("validation_commands", [])
        result.setdefault("failure_category", "unknown")
        result.setdefault("manual_triage_minutes", 45)
        result.setdefault("auto_triage_minutes", 5)
        return result
    except Exception as e:
        return {
            "root_cause": f"LLM analysis unavailable: {e}",
            "reasoning_steps": ["LLM unavailable, fallback to manual review"],
            "confidence": 0.0,
            "risk_score": 50,
            "files_touched": changed_files,
            "patch": "",
            "summary": "Fallback: manual review required",
            "validation_commands": [],
            "failure_category": "unknown",
            "manual_triage_minutes": 45,
            "auto_triage_minutes": 5,
        }

def suggest_mr_title(analysis: dict) -> str:
    return f"fix: {analysis.get('summary', 'auto-fix CI failure')[:60]}"
