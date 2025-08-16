dev:
	pip install -r requirements.txt
	ENV_FILE=.env.dev python -m galactia.main
prod:
	venv/bin/pip install -r requirements.txt
	ENV_FILE=.env.prod venv/bin/python -m galactia.main