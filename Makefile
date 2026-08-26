.PHONY: venv install demo demo-real test test-unit test-integration clean

venv:
	python3.11 -m venv venv
	venv/bin/pip install -r requirements.txt
	venv/bin/pip install pytest

install: venv

# Mock mode: boots the app against the in-memory GitLab emulator.
# No external GitLab needed; the AI card runs the real (or offline) agent.
demo:
	GITLAB_MODE=mock FLASK_PORT=18080 venv/bin/python app/main.py

# Real mode: bring up GitLab CE + Runner + app via docker compose.
demo-real:
	docker compose -f docker-compose.gitlab.yml up -d

# Unit tests only (the repo's own CI unit stage runs the same set).
# The integration suite is INTENTIONALLY red (it is the demo's failure);
# it runs in the `integration` CI stage, not here.
test test-unit:
	venv/bin/python -m pytest -q tests/unit

# Integration tests (expected to fail until the GenAI fix is merged).
test-integration:
	venv/bin/python -m pytest -q tests/integration

clean:
	-rm -rf venv
