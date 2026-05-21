.PHONY: dev prod migrate-dev compile test summary-local

dev:
	pip install -r requirements.txt
	ENV_FILE=.env.dev python -m galactia.main

migrate-dev:
	ENV_FILE=.env.dev python -m alembic upgrade head

compile:
	python -m compileall galactia tests scripts

test:
	python -m pytest -q

summary-local:
	python scripts/test_summary_flow_local.py

prod:
	venv/bin/pip install -r requirements.txt
	ENV_FILE=.env.prod venv/bin/python -m galactia.main
