# panel/deps.py
from datetime import datetime
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from core.models import User, Guild, GuildMember, PremiumMembership

def require_auth_request(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user

def get_user_db(request: Request, db: Session) -> User:
    sess = require_auth_request(request)
    u = db.get(User, int(sess["id"])) if "id" in sess else None
    if not u:
        raise HTTPException(401, "User not found")
    return u

def require_site_admin(request: Request, db: Session) -> User:
    u = get_user_db(request, db)
    if not u.is_site_admin:
        raise HTTPException(403, "Admin only")
    return u

def require_guild_admin(request: Request, guild_id: int, db: Session) -> Guild:
    sess = require_auth_request(request)
    g = db.execute(select(Guild).where(Guild.discord_id == guild_id)).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Guild not found")
    gm = db.execute(select(GuildMember).where(and_(
        GuildMember.guild_id == g.id,
        GuildMember.user_id == int(sess["id"])
    ))).scalar_one_or_none()
    if not gm or gm.role not in ("owner", "admin", "officer"):
        raise HTTPException(403, "Guild admin only")
    return g

def has_premium(user_db_id: int, guild_db_id: int, db: Session) -> bool:
    pm = db.execute(select(PremiumMembership).where(and_(
        PremiumMembership.user_id == user_db_id,
        PremiumMembership.guild_id == guild_db_id,
        (PremiumMembership.expires_at.is_(None)) | (PremiumMembership.expires_at > datetime.utcnow())
    ))).scalar_one_or_none()
    return pm is not None
