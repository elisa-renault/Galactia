# panel/routers/admin.py
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select
from core.db import get_db
from core.models import User, Guild, GuildPremium
from panel.deps import require_site_admin
from datetime import datetime

templates = Jinja2Templates(directory="panel/templates")
router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/premium", response_class=HTMLResponse)
def premium_index(request: Request, db: Session = Depends(get_db)):
    me = require_site_admin(request, db)

    guilds = db.execute(select(Guild).order_by(Guild.name)).scalars().all()

    rows = db.execute(
        select(GuildPremium, Guild, User)
        .join(Guild, GuildPremium.guild_id == Guild.id)
        .outerjoin(User, GuildPremium.granted_by == User.id)
        .where(
            (GuildPremium.expires_at.is_(None)) |
            (GuildPremium.expires_at > datetime.utcnow())
        )
        .order_by(Guild.name)
    ).all()

    premiums = []
    for gp, g, u in rows:
        premiums.append({
            "guild_db_id": g.id,
            "guild_discord_id": g.discord_id,
            "guild_name": g.name,
            "tier": gp.tier,
            "expires_at": gp.expires_at,
            "granted_by": (u.display_name or u.username) if u else None,
        })

    return templates.TemplateResponse(
        "admin_premium.html",
        {
            "request": request,
            "user": request.session.get("user"),
            "guilds": guilds,
            "premiums": premiums,   # 👈 AJOUT
        },
    )

@router.post("/premium/grant")
def premium_grant(request: Request, guild_id: int = Form(...),
                  tier: str = Form("premium"), expires_at: str | None = Form(None),
                  db: Session = Depends(get_db)):
    me = require_site_admin(request, db)
    from datetime import datetime
    g = db.get(Guild, guild_id)
    if not g:
        raise HTTPException(404, "Guild not found")
    gp = db.execute(select(GuildPremium).where(
        GuildPremium.guild_id == guild_id
    )).scalar_one_or_none()
    if not gp:
        gp = GuildPremium(guild_id=guild_id, tier=tier, granted_by=me.id)
        db.add(gp)
    else:
        gp.tier = tier
        gp.granted_by = me.id
    if expires_at:
        gp.expires_at = datetime.fromisoformat(expires_at)
    else:
        gp.expires_at = None
    db.commit()
    return RedirectResponse(url="/admin/premium", status_code=303)

@router.post("/premium/revoke")
def premium_revoke(request: Request, guild_id: int = Form(...),
                   db: Session = Depends(get_db)):
    me = require_site_admin(request, db)
    gp = db.execute(select(GuildPremium).where(
        GuildPremium.guild_id == guild_id
    )).scalar_one_or_none()
    if gp:
        db.delete(gp)
        db.commit()
    return RedirectResponse(url="/admin/premium", status_code=303)
