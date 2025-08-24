from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select
from core.db import get_db
from core.models import Guild, GuildMember, GuildSetting

templates = Jinja2Templates(directory="panel/templates")
router = APIRouter()

def require_auth_and_guild(request: Request, db: Session):
    user = request.session.get("user")
    gid = request.session.get("active_guild_id")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    if not gid:
        raise HTTPException(status_code=400, detail="No guild selected.")

    guild = db.query(Guild).filter(Guild.discord_id == int(gid)).first()
    if not guild:
        raise HTTPException(status_code=403, detail="Guild not registered.")
    member = db.query(GuildMember).filter(
        GuildMember.user_id == int(user["id"]),
        GuildMember.guild_id == guild.id
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="No access to this guild.")
    return user, str(guild.discord_id), member.role

def get_or_create_setting(db: Session, guild_id_db: int, key: str, default: str=""):
    s = db.execute(
        select(GuildSetting).where(GuildSetting.guild_id==guild_id_db, GuildSetting.key==key)
    ).scalar_one_or_none()
    if not s:
        s = GuildSetting(guild_id=guild_id_db, key=key, value=default)
        db.add(s)
        db.commit()
    return s

@router.get("/g/{guild_id}/settings", response_class=HTMLResponse)
def settings_page(guild_id: str, request: Request, db: Session = Depends(get_db)):
    user, active_gid, role = require_auth_and_guild(request, db)
    if active_gid != guild_id:
        request.session["active_guild_id"] = guild_id

    guild = db.execute(select(Guild).where(Guild.discord_id==int(guild_id))).scalar_one()
    icon_url = f"https://cdn.discordapp.com/icons/{guild.discord_id}/{guild.icon}.png?size=64" if guild.icon else f"https://cdn.discordapp.com/embed/avatars/{guild.discord_id % 5}.png"

    return templates.TemplateResponse("settings.html", {
        "request": request, "user": user, "guild_id": guild_id, "role": role,
        "guild_name": guild.name, "guild_icon": icon_url
    })

