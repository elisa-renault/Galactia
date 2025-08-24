import time, secrets
from urllib.parse import urlencode
import httpx
from itsdangerous import URLSafeSerializer
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from core.settings import settings
from core.models import User
from datetime import datetime
from panel.auth.token_store import save_token

DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API = "https://discord.com/api"
SCOPES = ["identify", "guilds"]

def _sign_state(request: Request) -> str:
    s = URLSafeSerializer(settings.SESSION_SECRET, salt="oauth-state")
    return s.dumps({"t": int(time.time()), "ip": request.client.host})

def _verify_state(state: str) -> bool:
    s = URLSafeSerializer(settings.SESSION_SECRET, salt="oauth-state")
    try:
        s.loads(state)
        return True
    except Exception:
        return False

def login_redirect(request: Request) -> RedirectResponse:
    state = _sign_state(request)
    params = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.DISCORD_REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "prompt": "consent",
    }
    return RedirectResponse(url=f"{DISCORD_AUTH_URL}?{urlencode(params)}")

async def exchange_code_for_token(code: str):
    data = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "client_secret": settings.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.DISCORD_REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(DISCORD_TOKEN_URL, data=data, headers=headers, timeout=20.0)
        resp.raise_for_status()
        return resp.json()

async def get_user_me(access_token: str):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {access_token}"}, timeout=15.0)
        r.raise_for_status()
        return r.json()

async def get_user_guilds(access_token: str):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        r.raise_for_status()
        return r.json()

async def handle_callback(request: Request, db: Session):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state or not _verify_state(state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    # 1) Récupérer token
    token = await exchange_code_for_token(code)

    # 2) Fetch user info via Discord
    me = await get_user_me(token["access_token"])
    did = int(me["id"])
    username_tag = f'{me.get("username")}#{me.get("discriminator", "0")}'
    display = me.get("global_name") or me.get("username")

    # 3) Synchroniser en DB
    user = db.query(User).filter(User.discord_id == did).first()
    if not user:
        user = User(
            discord_id=did,
            username=username_tag,
            display_name=display,
            avatar=me.get("avatar"),
            created_at=datetime.utcnow(),
        )
        db.add(user)
    else:
        user.username = username_tag
        user.display_name = display
        user.avatar = me.get("avatar")
    user.last_login = datetime.utcnow()
    db.commit()

    # 4) Créer un identifiant de session (_sid) si absent
    session_id = request.session.get("_sid")
    if not session_id:
        session_id = secrets.token_urlsafe(24)
        request.session["_sid"] = session_id

    # 5) Sauvegarder access_token côté serveur (cache/Redis)
    save_token(session_id, token["access_token"])

    # 6) Stocker uniquement les infos safe dans la session
    request.session["user"] = {
        "id": str(user.id),
        "discord_id": str(did),
        "username": user.display_name or user.username,
        "is_site_admin": bool(user.is_site_admin),
    }

    return RedirectResponse(url="/select-guild")