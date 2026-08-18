.PHONY: venv install demo demo-real test clean

venv:
	python3.11 -m venv venv
	venv/bin/pip install -r requirements.txt
	venv/bin/pip install pytest

install: venv

# Mock mode: boots the app against the in-memory GitLab emulator and seeds a
# failing pipeline, then drives the full auto-fix flow. No external GitLab needed.
demo:
	GITLAB_MODE=mock FLASK_PORT=18080 venv/bin/python app/main.py & \
	sleep 2; \
	GITLAB_MODE=mock venv/bin/python scripts/seed_demo.py; \
	echo "Demo running at http://localhost:18080 (mock GitLab)";

# Real mode: bring up GitLab CE + Runner + app via docker compose.
demo-real:
	docker compose -f docker-compose.gitlab.yml up -d

test:
	venv/bin/python -m pytest -q tests/

clean:
	-rm -rf venv
