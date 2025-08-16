dev:
	pip install -r requirements.txt
	ENV_FILE=.env.dev python main.py
prod:
	venv/bin/pip install -r requirements.txt
	ENV_FILE=.env.prod venv/bin/python main.py