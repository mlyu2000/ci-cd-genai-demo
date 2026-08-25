"""GenAI agent: analyze CI failures and synthesize an auto-fix patch.

Live LLM (CS1 / vLLM) is ALWAYS the primary path — including mock mode.
Curated scenarios are used ONLY as (a) the failing-code context (file contents
that the LLM reads) and (b) an offline fallback if the LLM is unreachable.
Every analysis is tagged with its source: "live-llm" or "offline-fallback".
"""

import os
import json
import re
import time
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


def _call_llm(user_msg: str) -> dict:
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
    r = requests.post(f"{LLM_ENDPOINT}/chat/completions", json=payload, headers=headers,
                      timeout=120, verify=SSL_CA_CERT or True)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    # Strip any <think>...</think> reasoning blocks some models emit, then parse JSON.
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    result = json.loads(content)
    for k, v in (("reasoning_steps", []), ("validation_commands", []),
                 ("failure_category", "unknown"), ("manual_triage_minutes", 45),
                 ("auto_triage_minutes", 5)):
        result.setdefault(k, v)
    return result


def analyze_failure(job_trace: str, changed_files: list, commit_msg: str = "",
                    file_contents: dict = None, git_diff: str = "",
                    scenario: dict = None) -> dict:
    """Analyze a failing job. Always calls the LLM first; the scenario (if any)
    provides the failing source files as context and serves as the offline
    fallback only. Returns analysis + provenance metadata."""

    # Failing-code context: prefer explicit file_contents, else the scenario's
    # "before" file (the code as it existed when the pipeline failed).
    ctx = dict(file_contents or {})
    if scenario and scenario.get("changed_file") and scenario.get("file_before"):
        ctx.setdefault(scenario["changed_file"], scenario["file_before"])

    file_context = ""
    for path, content in ctx.items():
        file_context += f"\n--- {path} ---\n{content}\n"

    user_msg = (
        f"Commit message: {commit_msg or 'unknown'}\n"
        f"Changed files: {', '.join(changed_files) or 'unknown'}\n"
        f"Git diff:{git_diff[:2000] if git_diff else ' (not provided — infer the fix from the code below)'}\n"
        f"File contents (as they existed at the failing commit):{file_context}\n"
        f"Failing job trace:\n{job_trace[-4000:]}"
    )

    t0 = time.time()
    try:
        result = _call_llm(user_msg)
        result["_source"] = "live-llm"
        result["_llm_model"] = LLM_MODEL
        result["_llm_endpoint"] = LLM_ENDPOINT
        result["_llm_latency_ms"] = int((time.time() - t0) * 1000)
        return result
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        # Offline fallback: curated scenario (keeps the demo alive without LLM).
        if scenario:
            fb = {
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
        else:
            fb = {
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
        fb["_source"] = "offline-fallback"
        fb["_llm_model"] = LLM_MODEL
        fb["_llm_endpoint"] = LLM_ENDPOINT
        fb["_llm_latency_ms"] = latency
        fb["_fallback_error"] = str(e)
        return fb


def suggest_mr_title(analysis: dict) -> str:
    return f"fix: {analysis.get('summary', 'auto-fix CI failure')[:60]}"
