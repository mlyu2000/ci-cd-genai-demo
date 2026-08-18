# CI/CD GenAI Demo

A self-contained demo showing a GenAI agent that **triages CI/CD failures and
opens a real auto-fix merge request**. Built around GitLab CI/CD + a CS1 LLM
(LiteLLM proxy). HPE-themed live dashboard with light/dark mode, live pipeline
timeline, streaming AI root-cause, before/after diff viewer, and executive KPIs.

## Architecture

```
                 ┌─────────────┐   webhook / poll   ┌──────────────────────┐
 Git push ──────▶│  GitLab CE  │ ─────────────────▶ │  Flask demo app      │
 (or `make demo` │  (real) OR  │                    │  - /api/poll         │
  mock seed)     │  in-memory  │ ◀──── auto-fix MR ──│  - /api/approve      │
                 │  mock       │      (branch+commit)│  - genai_agent.py    │
                 └─────────────┘                    │  - webhook.py        │
                                                    │  - gitlab_mock.py    │
                 ┌─────────────┐  chat/completions   └──────────────────────┘
                 │ CS1 LiteLLM │ ◀───────────────────────────┘
                 │  (LLM)      │
                 └─────────────┘
```

- **`app/main.py`** — Flask UI + API (poll, analyze, approve, metrics, SSE).
- **`app/webhook.py`** — GitLab backend dispatch (real v4 REST *or* mock), webhook
  ingestion, SQLite event/KPI store.
- **`app/gitlab_mock.py`** — in-memory GitLab emulator (no Docker needed).
- **`app/genai_agent.py`** — calls the LLM with a strict JSON contract
  `{root_cause, confidence, risk_score, patch, summary}` and degrades gracefully.
- **`docker-compose.gitlab.yml`** — real GitLab CE + Runner (the "true" path).

## Modes

`GITLAB_MODE` (in `.env`) selects the backend:

| Mode | Backend | Boot time | Use |
|------|---------|-----------|-----|
| `mock` | `gitlab_mock.py` | <5s | Demos, CI, when GitLab is slow/down |
| `real` | GitLab CE via compose | ~5–10 min | Full fidelity, real MRs |

## Run

### Mock mode (fastest, zero external deps)
```bash
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pip install pytest
GITLAB_MODE=mock FLASK_PORT=18080 venv/bin/python app/main.py &
GITLAB_MODE=mock venv/bin/python scripts/seed_demo.py
# open http://localhost:18080
```
Or simply: `make demo`

### Real mode (GitLab CE + Runner)
```bash
cp .env.example .env   # fill LLM_API_KEY + GITLAB_TOKEN
docker compose -f docker-compose.gitlab.yml up -d
# create project in GitLab, set GITLAB_PROJECT_ID, register runner, push repo
make demo-real
```

## Demo flow (the "wow")
1. A pipeline fails at **Integration Tests** (flaky showcase injection).
2. Dashboard shows live stage dots → red on integration.
3. AI card reveals root cause (typing effect) + confidence/risk gauges + diff.
4. **Approve Auto-Fix** → agent opens a real MR containing the patch file; pipeline
   flips to green; executive KPIs (Auto-Fix Rate, MTTR, Risk) update from SQLite.
5. *Autonomous mode* toggle: skip approval, auto-merge on green (risk-gated).

> The integration stage deliberately fails ~70% of the time — it is a showcase
> injection, not a product test. See `docs/ai-capabilities.md`.

## Tests
```bash
venv/bin/python -m pytest -q tests/
```
The GitLab CI `unit-test` stage runs these for real (no `|| true` passthrough).

## Notes
- `.env` is gitignored — keep secrets out of git.
- `GITLAB_MODE=real` requires the compose stack up and a registered runner.
- LLM unreachable → `genai_agent` returns a structured "manual review" fallback,
  the UI keeps working.
