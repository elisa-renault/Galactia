from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from panel.auth.discord import get_user_guilds
from core.db import get_db
from core.models import Guild, GuildMember

templates = Jinja2Templates(directory="panel/templates")
router = APIRouter()

# Token store (sécurisé)
try:
    from panel.auth.token_store import get_token as _get_token
except Exception:
    _get_token = None

def _require_access_token(request: Request) -> str:
    sid = request.session.get("_sid")
    if not sid or not _get_token:
        raise HTTPException(401, "Session invalide, reconnecte-toi.")
    tok = _get_token(sid)
    if not tok:
        raise HTTPException(401, "Session expirée, reconnecte-toi.")
    return tok

def require_auth(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user

def has_manage_guild(g: dict) -> bool:
    perms = int(g.get("permissions", 0))
    return g.get("owner", False) or (perms & 0x20) == 0x20  # 0x20 = Manage Guild

CDN_ICON = "https://cdn.discordapp.com/icons/{gid}/{hash}.png?size=128"
EMBED_AVATAR = "https://cdn.discordapp.com/embed/avatars/{i}.png"  # 0..4

def guild_icon_url(g):
    if g.get("icon"):
        return CDN_ICON.format(gid=g["id"], hash=g["icon"])
    i = int(g["id"]) % 5
    return EMBED_AVATAR.format(i=i)

@router.get("/select-guild", response_class=HTMLResponse)
async def select_guild(request: Request):
    user = require_auth(request)
    access_token = _require_access_token(request)
    guilds = await get_user_guilds(access_token)
    allowed = [g for g in guilds if has_manage_guild(g)]
    for g in allowed:
        g["icon_url"] = guild_icon_url(g)
    return templates.TemplateResponse(
        "select_guild.html",
        {"request": request, "user": user, "guilds": allowed},
    )

@router.get("/g/{guild_id}")
async def choose_guild(guild_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request)
    gid = int(guild_id)

    access_token = _require_access_token(request)
    guilds = await get_user_guilds(access_token)
    ginfo = next((g for g in guilds if int(g["id"]) == gid), None)
    if not ginfo or not has_manage_guild(ginfo):
        raise HTTPException(status_code=403, detail="No access to this guild.")

    # Upsert Guild
    guild = db.query(Guild).filter(Guild.discord_id == gid).first()
    if not guild:
        guild = Guild(discord_id=gid, name=ginfo.get("name",""), icon=ginfo.get("icon"))
        db.add(guild)
        db.commit()
    else:
        changed = False
        if guild.name != ginfo.get("name"):
            guild.name = ginfo.get("name"); changed = True
        if guild.icon != ginfo.get("icon"):
            guild.icon = ginfo.get("icon"); changed = True
        if changed:
            db.commit()

    # Upsert GuildMember
    uid = int(request.session["user"]["id"])
    gm = db.query(GuildMember).filter(
        GuildMember.user_id == uid,
        GuildMember.guild_id == guild.id
    ).first()
    if not gm:
        gm = GuildMember(user_id=uid, guild_id=guild.id, role="admin")
        db.add(gm)
        db.commit()

    request.session["active_guild_id"] = guild_id
    return RedirectResponse(url=f"/g/{guild_id}/settings")
