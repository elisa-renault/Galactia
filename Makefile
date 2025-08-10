dev:
	@echo "🔧 Lancement de Galactia en mode DEV avec .env.dev"
	ENV_FILE=.env.dev python main.py

prod:
	@echo "🚀 Lancement de Galactia en mode PROD avec .env.prod"
	ENV_FILE=.env.prod python main.py
