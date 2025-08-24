# panel/routers/admin.py
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from core.db import get_db
from core.models import User, Guild, PremiumMembership
from panel.deps import require_site_admin
from datetime import datetime

templates = Jinja2Templates(directory="panel/templates")
router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/premium", response_class=HTMLResponse)
def premium_index(request: Request, db: Session = Depends(get_db)):
    me = require_site_admin(request, db)

    users = db.execute(select(User).order_by(User.id.desc()).limit(50)).scalars().all()
    guilds = db.execute(select(Guild).order_by(Guild.name)).scalars().all()

    rows = db.execute(
        select(PremiumMembership, Guild, User)
        .join(Guild, PremiumMembership.guild_id == Guild.id)
        .join(User, PremiumMembership.user_id == User.id)
        .where(
            (PremiumMembership.expires_at.is_(None)) |
            (PremiumMembership.expires_at > datetime.utcnow())
        )
        .order_by(Guild.name, User.display_name, User.username)
    ).all()

    premiums = []
    for pm, g, u in rows:
        premiums.append({
            "guild_db_id": g.id,
            "guild_discord_id": g.discord_id,
            "guild_name": g.name,
            "user_id": u.id,
            "user_name": u.display_name or u.username,
            "tier": pm.tier,
            "expires_at": pm.expires_at,
        })

    return templates.TemplateResponse(
        "admin_premium.html",
        {
            "request": request,
            "user": request.session.get("user"),
            "users": users,
            "guilds": guilds,
            "premiums": premiums,   # 👈 AJOUT
        },
    )

@router.post("/premium/grant")
def premium_grant(request: Request, user_id: int = Form(...), guild_id: int = Form(...),
                  tier: str = Form("premium"), expires_at: str | None = Form(None),
                  db: Session = Depends(get_db)):
    me = require_site_admin(request, db)
    from datetime import datetime
    g = db.get(Guild, guild_id)
    u = db.get(User, user_id)
    if not u or not g:
        raise HTTPException(404, "User or guild not found")
    pm = db.execute(select(PremiumMembership).where(
        PremiumMembership.user_id==user_id, PremiumMembership.guild_id==guild_id
    )).scalar_one_or_none()
    if not pm:
        pm = PremiumMembership(user_id=user_id, guild_id=guild_id, tier=tier)
        db.add(pm)
    pm.tier = tier
    if expires_at:
        pm.expires_at = datetime.fromisoformat(expires_at)
    db.commit()
    return RedirectResponse(url="/admin/premium", status_code=303)

@router.post("/premium/revoke")
def premium_revoke(request: Request, user_id: int = Form(...), guild_id: int = Form(...),
                   db: Session = Depends(get_db)):
    me = require_site_admin(request, db)
    pm = db.execute(select(PremiumMembership).where(
        PremiumMembership.user_id==user_id, PremiumMembership.guild_id==guild_id
    )).scalar_one_or_none()
    if pm:
        db.delete(pm); db.commit()
    return RedirectResponse(url="/admin/premium", status_code=303)
