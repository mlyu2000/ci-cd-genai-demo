# CI/CD GenAI Demo — Detailed Execution Plan
**Repo:** ~/projects/ci-cd-genai-demo  |  GitHub: mlyu2000/ci-cd-genai-demo
**Model:** muse-glimmer-30b via litellm-cs1 (base_url https://litellm.aie.cs1.ctc.sg.lab/v1)

## 0. Context
Current state:
- Flask demo app with Docker + .env LLM config
- UI shows pipeline stages, commit files, debug console
- Need real GitLab + GitLab CI + GenAI auto-fix loop with wow moment

## 1. Infrastructure — GitLab + Runner
**Files to create**
- `docker-compose.gitlab.yml`
```yaml
version: '3.8'
services:
  gitlab:
    image: gitlab/gitlab-ce:16.11.0-ce.0
    container_name: gitlab
    hostname: gitlab.local
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://localhost:8929'
        gitlab_rails['smtp_enable'] = false
    ports:
      - '8929:80'
      - '8443:443'
    volumes:
      - gitlab_config:/etc/gitlab
      - gitlab_logs:/var/log/gitlab
      - gitlab_data:/var/opt/gitlab
    restart: unless-stopped

  runner:
    image: gitlab/gitlab-runner:latest
    container_name: gitlab-runner
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - runner_config:/etc/gitlab-runner
    restart: unless-stopped
volumes:
  gitlab_config:
  gitlab_logs:
  gitlab_data:
  runner_config:
```

**Actions**
1. `docker compose -f docker-compose.gitlab.yml up -d`
2. Wait 5-10min for first boot. Access http://localhost:8929
3. Set admin password via UI, create group `demo` and project `ci-cd-genai-demo`
4. Register Runner: GitLab → CI/CD → Runners → New runner → copy token. In runner container: `gitlab-runner register --url http://gitlab:80 --registration-token <TOKEN> --executor docker --docker-image docker:24.0`
5. Verify runner is active.

## 2. Source Repo & CI Definition
**Files**
- `.gitlab-ci.yml`
```yaml
stages:
  - build
  - test
  - integration
  - deploy

variables:
  DOCKER_DRIVER: overlay2

build:
  stage: build
  script:
    - echo "Building app..."
    - docker build -t ci-cd-demo .
  artifacts:
    paths:
      - app

unit-test:
  stage: test
  script:
    - echo "Running unit tests..."
    - pytest -q || true

integration-test:
  stage: integration
  script:
    - echo "Running integration tests..."
    - python -c "import random; raise Exception('Flaky DB pool exhaustion in payments.test') if random.random()<0.7 else print('ok')"
  allow_failure: false

deploy:
  stage: deploy
  script:
    - echo "Deploying..."
  only:
    - main
```

**Actions**
1. Push current repo to GitLab project (mirror from GitHub or `git remote add gitlab http://gitlab.local/...`)
2. Commit `.gitlab-ci.yml`
3. Create MR `feature/genai-demo` → triggers pipeline. Ensure integration-test fails ~70% to showcase auto-fix.

## 3. Webhook & Event Ingestion
**Flask additions**
- `app/gitlab_webhook.py` — verify HMAC, parse events
- Endpoint `/webhook/gitlab`
  - `pipeline` events: update in-memory pipeline state
  - `job` events: capture logs, stage status
- Store state in SQLite `pipeline_state.db` with tables: pipelines, jobs, commits

**Actions**
1. Add `flask`, `requests`, `python-dotenv`, `gitpython` to requirements
2. Configure GitLab → Project → Webhooks → URL http://localhost:8080/webhook/gitlab, Secret `WEBHOOK_SECRET`
3. Test webhook via `curl` or manual pipeline run

## 4. GenAI Agent — Auto-Fix Loop
**Components**
- `app/genai_agent.py`
  - `analyze_failure(job_logs, changed_files)` → calls LiteLLM endpoint
  - Prompt template:
    ```
    You are a CI/CD GenAI agent. Given pipeline failure logs and changed files, identify root cause, confidence %, and propose a minimal patch.
    Output JSON: {root_cause, confidence, suggested_patch, risk_score, files_touched}
    ```
  - `generate_patch()` returns unified diff
  - `create_mr()` via GitLab API to open MR with patch

**Flow**
1. Job fails → webhook triggers → fetch logs + changed files
2. GenAI analyzes → produces root cause + patch
3. UI shows AI insights panel with confidence, risk, patch preview
4. User clicks "Approve Auto-Fix" → Flask calls GitLab API to create MR with patch
5. MR pipeline re-runs → success → UI updates KPIs

**KPIs to track**
- MTTR, Auto-Fix Rate, Releases/week, Risk Score
- Store in SQLite, expose `/api/metrics`

## 5. UI — HPE Design System & Wow Moment
**Design tokens**
- Colors from HPE Design System: `--hpe-blue:#0078d4`, `--hpe-teal:#00bfa5`, `--hpe-slate:#1a1d23`
- Light/dark toggle via CSS variables
- Components: Pipeline timeline, Stage cards, Source diff list, AI insights card, Debug console (collapsible), Executive KPIs

**Wow moments**
- Real-time pipeline animation via Server-Sent Events `/stream`
- On failure: AI card appears with animated typing effect, confidence meter
- One-click Approve → MR created, pipeline restarts automatically
- Executive dashboard shows cost avoidance: "Saved 4h engineering time"

## 6. Demo Script
1. Push commit → pipeline starts
2. Integration test fails
3. AI analyzes logs in <30s, shows root cause + patch
4. Approve auto-fix → MR created, pipeline passes
5. Dashboard updates KPIs

## 7. Commit & Push Checklist
- [ ] docker-compose.gitlab.yml
- [ ] .gitlab-ci.yml
- [ ] app/gitlab_webhook.py
- [ ] app/genai_agent.py
- [ ] UI redesign with HPE tokens
- [ ] requirements.txt update
- [ ] .env.example with WEBHOOK_SECRET
- Commit & push to GitHub

## 8. Risks
- GitLab CE needs 4GB RAM; may need to limit services
- Runner docker-in-docker requires privileged socket
- LiteLLM rate limits → add retry with backoff
- Keep .env out of git

Next step: create docker-compose.gitlab.yml and .gitlab-ci.yml, then wire webhook.
