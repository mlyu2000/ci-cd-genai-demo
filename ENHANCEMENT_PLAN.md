# CI/CD GenAI Demo — Enhancement Plan

## Problem with current approach
- Full GitLab CE = ~2GB image, 4GB+ RAM, 5–10 min cold boot. Heavy for a repeatable exec demo.
- Prior plan wires one real GitLab. If it's down/slow, the "wow" demo dies.
- Phase 4 (auto-fix) was described but not yet engineered end-to-end.

## Enhancement 1 — Dual-mode backend (real + instant)
Provide BOTH so the demo never fails:
- `docker-compose.gitlab.yml` — real GitLab CE + Runner (the "true" path).
- `app/gitlab_mock.py` — a lightweight Flask-embedded mock that emulates GitLab's
  pipeline/job/webhook/MR REST surface. Lets the full auto-fix flow run in <5s with
  zero external deps. Toggle via `GITLAB_MODE=real|mock`.

Result: exec can watch the real GitLab, but the recorded demo / fallback uses mock.

## Enhancement 2 — Phase 4 auto-fix loop (engineered, not faked)
Concrete event chain, all real code:
1. Commit pushed → pipeline created (real or mock).
2. `integration-test` job fails (injected flaky error in `.gitlab-ci.yml`).
3. GitLab webhook POSTs `Job`/`Pipeline` event to `/webhook/gitlab` (HMAC-verified).
4. Agent fetches failing job trace + changed files from the commit.
5. `genai_agent.analyze()` calls LiteLLM/CS1 with a strict JSON contract:
   `{root_cause, confidence(0-1), risk_score(0-100), patch(unified diff), files_touched}`.
6. UI renders AI card: root cause, confidence gauge, risk gauge, syntax-highlighted diff.
7. User clicks **Approve Auto-Fix** → agent calls GitLab API to open MR with the patch.
8. MR pipeline re-runs → green. Metrics (MTTR, auto-fix rate) update live.
9. Optional **Autonomous mode**: skip approval, auto-merge on green (governance toggle).

## Enhancement 3 — Wow-moment UX
- **Live SSE** (`/stream`): pipeline trace, stage dots animate build→test→fail→fix→green.
- **AI thinking stream**: token-by-token root-cause reveal (typing effect).
- **Diff viewer**: before/after with line-level highlight + risk tag per file.
- **Executive KPI bar**: MTTR ↓, Auto-Fix Rate %, Releases/week, Risk Score — with
  sparkline trends and a ticking "engineering hours saved" counter.
- **One-command demo**: `make demo` boots app + mock, seeds a failing pipeline, runs the
  full auto-fix, all in one terminal.

## Enhancement 4 — HPE Design System
- Exact tokens: `--hpe-blue:#0078d4`, `--hpe-teal:#00bfa5`, `--hpe-slate:#1a1d23`,
  `--hpe-green:#5fc80a`, status colors per HPE palette.
- Light/Dark toggle persisted to localStorage; respects `prefers-color-scheme`.
- Card/button/typography spacing per HPE GreenLake console look.

## Enhancement 5 — Reproducibility & docs
- `README.md`: architecture diagram, run instructions (real + mock), demo script.
- `.env.example`: `GITLAB_MODE`, `GITLAB_URL`, `GITLAB_TOKEN`, `WEBHOOK_SECRET`, `LLM_*`.
- Seed script `scripts/seed_demo.py` to populate a failing pipeline on demand.

## Files to create/modify
- `app/gitlab_mock.py` (new) — mock GitLab API + webhook emitter
- `app/genai_agent.py` (new) — LLM analysis + MR creation
- `app/webhook.py` (new) — HMAC-verified webhook + SSE state store (SQLite)
- `app/main.py` (modify) — SSE stream, AI card, diff viewer, KPI bar, theme toggle
- `.gitlab-ci.yml` (new) — flaky integration stage
- `docker-compose.gitlab.yml` (exists) — real GitLab
- `Makefile` (new) — `make demo`
- `README.md`, `.env.example` (modify)

## Decision needed from you
Real GitLab CE is heavy. Do you want:
(A) Keep real GitLab CE as primary + mock as fallback (recommended), or
(B) Mock-only for speed, real GitLab optional, or
(C) Real GitLab CE only (heaviest, most "real").
