# CI/CD GenAI Demo

A self-contained demo showing a GenAI agent that **triages real CI/CD failures
and opens a real auto-fix merge request** — with CI itself verifying the fix.
Built around GitLab CI/CD + a live LLM (vLLM/CS1). HPE-themed live dashboard.

**Design rule: no fakes, no hallucinated metrics.** Every number on screen is
measured from this session or live GitLab data. If the LLM is down, the UI says
"fallback" and shows a clearly-labeled curated analysis. If the LLM patch
can't be applied, the MR says so and attaches the patch for a human.

## Architecture

```
                 ┌─────────────┐  trigger/poll   ┌──────────────────────────┐
 Run Pipeline ──▶│  GitLab CE  │ ──────────────▶ │  Flask demo app          │
 (button only,   │  + runner   │  approve: git   │  - live pipeline view    │
  no push-auto)  │  (real) OR  │  apply → branch │  - /api/analyze → LLM    │
                 │  in-memory  │  → real MR      │  - /api/approve (apply)  │
                 │  mock       │ ◀────────────── │  - /api/merge (real API) │
                 └─────────────┘  MR pipeline    └──────────────────────────┘
                 ┌─────────────┐  chat/completions   └──────────────────────────┘
                 │  LLM (vLLM/ │ ◀──────────────────── genai_agent.py
                 │  CS1)       │   retry 429/5xx, provenance badge
                 └─────────────┘
```

- `app/main.py` — Flask UI + API (poll, analyze, approve, merge, metrics, reset, healthz).
- `app/webhook.py` — GitLab backend dispatch (real v4 REST | in-memory mock), **measured KPIs**, real patch-apply (git worktree) + MR + merge.
- `app/genai_agent.py` — LLM analysis with strict JSON contract, trace cleaning, retries, offline fallback.
- `app/gitlab_mock.py` — in-memory GitLab emulator (demo without Docker).
- `app/db/pool.py` — the **real** config the integration test asserts against (the demo's deterministic failure).
- `tests/unit/` — green (CI unit stage). `tests/integration/` — red by design (CI integration stage; the failure the agent fixes).

## The demo loop (all real)

1. **▶ Run Pipeline** starts a real pipeline: Build → Unit (green) → Integration.
2. Integration runs `tests/integration/` against `app/db/pool.py` (pool=5, workers=6) → **deterministic pool-exhaustion failure**.
3. The **live LLM** (badge shows model + `live LLM` / `fallback`) receives the full job trace + the failing source file + commit diff and returns root cause, confidence, risk, and a patch.
4. **Approve Auto-Fix** applies the patch to the real files (git worktree → commit → push branch), opens a real MR. If `git apply` rejects the patch, it attaches the patch for review and says so — never fakes success.
5. The MR's **own pipeline verifies the fix** (test now passes). With **Autonomous** on, the MR is merged via the real GitLab merge API — only after green + risk gates.
6. KPIs are **measured**: auto-fix count (deduped per pipeline), last fix duration (failure → MR green), releases last 7 days (live GitLab), triage time saved this session. **Reset demo** zeroes the session counters.

## Run

### Real mode (GitLab CE + Runner) — recommended
```bash
python3.11 -m venv venv && venv/bin/pip install -r requirements.txt pytest
# .env: LLM_ENDPOINT/LLM_API_KEY/LLM_MODEL, GITLAB_MODE=real, GITLAB_URL, GITLAB_TOKEN, GITLAB_PROJECT_ID
# GitLab: project ci-cd-genai-demo pushed, runner registered (see docs/ below)
docker build -t ci-cd-genai-demo:app .
docker run -d --name ci-cd-genai-demo --network host \
  -e GITLAB_MODE=real -e GITLAB_PROJECT_ID=1 -e FLASK_PORT=18080 \
  -e LLM_ENDPOINT=... -e LLM_API_KEY=... -e LLM_MODEL=... \
  -e GITLAB_URL=... -e GITLAB_TOKEN=... \
  -v $PWD:/repo ci-cd-genai-demo:app
# open http://localhost:18080
```
Notes:
- `--network host` so the container can reach both GitLab and the LLM (internal DNS).
- `-v $PWD:/repo` gives the app a git checkout so **Approve Auto-Fix applies the patch for real** and pushes the fix branch (the gitlab remote must be authenticated in the repo).
- Pipeline source rules: pipelines only start on demand (button/MR), never on push.

### Mock mode (no GitLab, instant)
```bash
GITLAB_MODE=mock FLASK_PORT=18080 venv/bin/python app/main.py
# LLM still real if LLM_ENDPOINT is set; otherwise clearly-labeled offline fallback.
```

### Tests
```bash
make test            # unit suite (green) — same set the CI unit stage runs
make test-integration# integration (RED by design — it is the demo's failure)
```

## Demo runbook (presale script, ~8 min)

| # | Step | What the audience sees | Say |
|---|------|------------------------|-----|
| 1 | Click ▶ Run Pipeline | Real pipeline runs: build 9s ✅, unit 30s ✅, integration ❌ | "This is a real GitLab pipeline on a real runner. The integration stage fails **by design** — it loads the payments service with 6 workers against a 5-connection pool." |
| 2 | AI card appears (~6s) | Badge: `live LLM: qwen3.5-4b`; root cause + reasoning + confidence/risk | "Not canned text — that model just read the runner's trace and the source file. You can verify in the debug console which lines it saw." |
| 3 | Tabs: Reasoning / Patch / Validation / Manual Baseline | Diff to `app/db/pool.py`, validation commands, 45-min manual baseline | "The patch touches the real config file. This is the kind of fix a human would spend an afternoon on." |
| 4 | Approve Auto-Fix | "✅ Patch APPLIED to real files" + real MR link | "It applied the patch with git, pushed a branch, opened MR !N. If the model had hallucinated a hunk, it would say so instead of faking it." |
| 5 | Watch the MR's pipeline | New pipeline runs the **fixed** code → green; KPI "Last auto-fix time" ticks to the measured value | "The fix is only done when **our own CI** says it's done. That measured number is the MTTR — not a seed." |
| 6 | (Optional) Autonomous on + re-run | MR merges itself via the GitLab API after green + gates | "With the risk gates passing, the last click is optional. The gates are the governance story." |
| 7 | Point at KPIs | All `--`/measured, "Reset demo" available | "No fabricated numbers. Everything on screen is either this session or live GitLab. I can reset it live." |

**Consistency guarantees (what the demo never does):**
- Never inflates KPIs (per-poll counters are gone; releases = real 7-day count).
- Never claims a fix applied when `git apply` failed (explicit "attached for review").
- Never auto-merges without green MR pipeline + gates (real API call, logged).
- Never hides LLM fallback (badge + debug console both say "fallback").

## Runbooks / ops

- **GitLab setup (once):** `docker compose -f docker-compose.gitlab.yml up -d`; set admin password; create project `ci-cd-genai-demo` (push the repo); register a docker-executor runner. Set `GITLAB_TOKEN` (glpat) in `.env`.
- **LLM (once):** any OpenAI-compatible endpoint works (vLLM, CS1 LiteLLM, etc.). Set `LLM_ENDPOINT`/`LLM_MODEL`. Retries: 3× on 429/5xx.
- **Reset a messy demo:** click **Reset demo** (zeroes session KPIs) or `docker restart` (also clears in-memory state). Closed MRs/pipelines in GitLab are history — leave them; they're proof, not noise.
- **Health check:** `curl http://localhost:18080/healthz` → app up + LLM reachable.

## File map
```
app/main.py            UI + API (Flask)
app/webhook.py         GitLab real/mock dispatch, measured KPIs, apply+MR+merge
app/genai_agent.py     LLM prompt/contract, retries, trace cleaning, fallback
app/gitlab_mock.py     in-memory GitLab (mock mode)
app/db/pool.py         real config the integration test asserts on
tests/unit/            green unit tests (agent, kpis, app, served-JS syntax)
tests/integration/     red-by-design demo failure
scripts/seed_demo.py   (legacy) mock seed helper
Dockerfile / docker-compose.gitlab.yml / .gitlab-ci.yml / Makefile
```
