# Galactia Dev And Production PostgreSQL

## Local Development On Windows

Use a local PostgreSQL database for development. Do not point `.env.dev` at the
production VPS database.

1. Install Docker Desktop for Windows.
2. Start the development database:

```powershell
docker compose -f docker-compose.dev.yml up -d
```

3. Use this database URL in `.env.dev`:

```env
ENV_MODE=dev
DATABASE_URL=postgresql+asyncpg://galactia_dev:galactia_dev_password@127.0.0.1:55432/galactia_dev
```

4. Install dependencies and run migrations:

```powershell
python -m pip install -r requirements.txt
$env:ENV_FILE=".env.dev"; python -m alembic upgrade head
```

5. Optionally import legacy JSON data into the local development database:

```powershell
$env:ENV_FILE=".env.dev"; python scripts/migrate_json_to_postgres.py --dry-run
$env:ENV_FILE=".env.dev"; python scripts/migrate_json_to_postgres.py
```

6. Start the bot:

```powershell
$env:ENV_FILE=".env.dev"; python -m galactia.main
```

Useful development commands:

```powershell
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml logs postgres
docker compose -f docker-compose.dev.yml down
```

## Production On Debian VPS

Production uses PostgreSQL on the same VPS as the bot. PostgreSQL should listen
only on localhost, and port `5432` should not be exposed publicly.

Defaults:

- App path: `/home/Galactia`
- App user: `deploy`
- Env file: `/home/Galactia/.env.prod`
- Database: `galactia_prod`
- Database role: `galactia_app`
- systemd service: `galactia.service`
- Database URL: `postgresql+asyncpg://galactia_app:<password>@127.0.0.1:5432/galactia_prod`

Install Codex CLI on the VPS:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates build-essential python3 python3-venv python3-pip postgresql postgresql-contrib nodejs bubblewrap

node -v
npm -v

mkdir -p ~/.local/npm-global
npm config set prefix "$HOME/.local/npm-global"
printf '\nexport PATH="$HOME/.local/npm-global/bin:$PATH"\n' >> ~/.profile
. ~/.profile

npm i -g @openai/codex
codex --version
codex
```

If browser authentication is not practical over SSH, use a dedicated OpenAI API
key for the session:

```bash
export OPENAI_API_KEY="sk-..."
codex
```

## Prompt For Codex On The VPS

Run this from Codex on the VPS:

```text
Tu es sur mon VPS Debian de production pour le projet Galactia.

Contexte important :
- Le repo Galactia existe deja dans `/home/Galactia`.
- Utilise ce repo existant.
- Ne clone pas ailleurs.
- Ne deplace pas le projet.
- L'utilisateur Linux qui gere le deploiement est `deploy`.
- Le repo peut contenir un stash Git nomme `vps local changes before postgres deploy`; ne l'applique pas sans confirmation.
- Le dossier `.cache/` peut exister et n'est pas important pour le deploiement.

Objectif : deployer Galactia en production avec PostgreSQL local sur le meme VPS, sans exposer PostgreSQL sur Internet.

Contraintes :
- Ne supprime aucune donnee existante sans confirmation explicite.
- Ne loggue jamais les secrets.
- PostgreSQL doit ecouter uniquement en local.
- La base de production s'appelle `galactia_prod`.
- Le role applicatif s'appelle `galactia_app`.
- Le service systemd s'appelle `galactia.service`.
- Le fichier d'environnement prod est `/home/Galactia/.env.prod`, permissions `600`, proprietaire `deploy`.
- Utilise `DATABASE_URL=postgresql+asyncpg://galactia_app:<password>@127.0.0.1:5432/galactia_prod`.
- Si une ancienne URL Supabase est fournie, commence par compter les lignes dans `guild_settings`, `twitch_follows`, `youtube_follows` et demande confirmation avant migration.
- Si aucune ancienne URL Supabase n'est fournie, initialise une base vide avec Alembic.

Travail a faire :
1. Inspecter l'etat du VPS : Debian, Python, PostgreSQL, systemd, espace disque, utilisateur courant, chemin du repo.
2. Installer les dependances systeme manquantes.
3. Aller dans `/home/Galactia` et verifier `git status --short`, `git log --oneline -1`, et la presence du commit `Configure PostgreSQL dev and prod environments`.
4. Ne pas appliquer le stash Git existant sans confirmation.
5. Creer ou mettre a jour le venv Python dans `/home/Galactia/venv`.
6. Installer `requirements.txt`.
7. Configurer PostgreSQL local : DB, role, mot de passe fort, privileges minimaux.
8. Creer `/home/Galactia/.env.prod` en me demandant les secrets manquants sans les afficher.
9. Executer `ENV_FILE=/home/Galactia/.env.prod /home/Galactia/venv/bin/alembic upgrade head` depuis le repo.
10. Creer un service systemd `galactia.service` qui lance `/home/Galactia/venv/bin/python -m galactia.main` avec `User=deploy` et `WorkingDirectory=/home/Galactia`.
11. Activer et demarrer le service.
12. Ajouter une sauvegarde PostgreSQL locale quotidienne avec retention 7 jours dans `/var/backups/galactia`.
13. Creer un timer systemd pour cette sauvegarde.
14. Verifier `systemctl status galactia`, les logs recents, la connexion DB et la presence des tables.
15. Me donner un resume final avec les chemins crees, les commandes de maintenance et les risques restants.

Ne fais pas de migration destructive. Si une etape necessite un secret ou une decision irreversible, arrete-toi et demande confirmation.
```

## Backup Note

The VPS backup described above is local only. It protects against accidental
changes, but not against losing the VPS. Add an offsite target before treating
the production setup as fully protected.

## References

- OpenAI Codex CLI: https://developers.openai.com/codex/cli
- Codex install requirements: https://github.com/openai/codex/blob/main/docs/install.md
