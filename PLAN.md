# CI/CD GenAI Demo — GitLab Integration Plan
**Goal:** Real GitLab + GitLab CI/CD + GenAI demo with a wow-moment UI for business/executive users.

## 1. Architecture
- **GitLab Community Edition** running in Docker (self-hosted). Port 8080 → 8929 host.
- **GitLab CI Runner** (docker executor) for real pipelines.
- **Demo app** (Flask) shows live pipeline status, commit sources, AI root-cause + auto-fix suggestions.
- **GitLab Webhooks** → demo app receives pipeline events (push, job failure).
- **LiteLLM/CS1** provides LLM for GenAI analysis (root-cause, patch generation).
- **HPE Design System** colors/themes + light/dark toggle.

## 2. Deliverables
1. `docker-compose.gitlab.yml` — GitLab CE + Postgres + Redis + Runner.
2. `.gitlab-ci.yml` — Build → Test → Integration → Deploy stages with intentional flaky test.
3. `gitlab-webhook` endpoint in Flask (`/webhook/gitlab`) to ingest events.
4. UI redesign:
   - Pipeline timeline with live stage status.
   - Source files changed in commit.
   - AI insights panel: root cause, confidence, suggested fix, risk score.
   - Collapsible debug console.
   - HPE palette, light/dark theme toggle.
   - Executive KPIs: MTTR ↓, Auto-Fix Rate, Releases/week, Risk Score.
5. Demo data seed: sample repo `ci-cd-genai-demo` pushed to GitLab, MR triggers pipeline.

## 3. Steps
- **Step A:** Spin up GitLab CE with docker-compose (volumes for persistence).
- **Step B:** Configure external URL, admin user, runner token.
- **Step C:** Create project in GitLab, push demo repo.
- **Step D:** Add `.gitlab-ci.yml` with 3 stages + failing integration test.
- **Step E:** Add webhook listener in Flask, map GitLab events to UI state.
- **Step F:** Add GenAI analysis on failure: call LiteLLM to produce root cause & patch.
- **Step G:** Redesign UI with HPE design tokens, pipeline visualization, wow metrics.
- **Step H:** Record demo script + screenshots.

## 4. Wow Moment Design
- Live pipeline animation, real-time log streaming.
- AI auto-fix suggestion appears within seconds of failure.
- Risk score and cost avoidance displayed.
- One-click "Approve Auto-Fix" → MR created automatically.
- Executive dashboard: MTTR ↓82%, 12 releases/week, 87% auto-fix.

## 5. Risks & Notes
- GitLab CE needs ~4GB RAM; use limited `gitlab/gitlab-ce:16.11` image.
- Use host network for Runner to avoid TLS issues.
- Keep .env out of git; use docker-compose env_file.
