from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from core.db import get_db
from core.models import Feature, GuildFeatureFlag
from panel.deps import require_auth_request, require_guild_admin, has_premium
from datetime import datetime

templates = Jinja2Templates(directory="panel/templates")
router = APIRouter()

@router.get("/g/{guild_id}/features", response_class=HTMLResponse)
def features_page(guild_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth_request(request)
    g = require_guild_admin(request, int(guild_id), db)
    if not has_premium(g.id, db):
        raise HTTPException(403, "Premium required for this guild")

    features = db.execute(select(Feature).order_by(Feature.id)).scalars().all()
    flags_rows = db.execute(select(GuildFeatureFlag).where(GuildFeatureFlag.guild_id == g.id)).scalars().all()
    flags = {f.feature_id: f for f in flags_rows}
    view = []
    for ft in features:
        flag = flags.get(ft.id)
        view.append({"id": ft.id, "key": ft.key, "name": ft.name, "enabled": flag.enabled if flag else False})
    return templates.TemplateResponse("guild_features.html", {"request": request, "user": user, "guild_id": guild_id, "features": view})

@router.post("/g/{guild_id}/features/toggle")
def toggle_feature(guild_id: str, request: Request,
                   feature_id: int = Form(...), enabled: int = Form(...),
                   db: Session = Depends(get_db)):
    user = require_auth_request(request)
    g = require_guild_admin(request, int(guild_id), db)
    if not has_premium(g.id, db):
        raise HTTPException(403, "Premium required")

    flag = db.execute(select(GuildFeatureFlag).where(and_(
        GuildFeatureFlag.guild_id == g.id,
        GuildFeatureFlag.feature_id == feature_id
    ))).scalar_one_or_none()
    if not flag:
        flag = GuildFeatureFlag(guild_id=g.id, feature_id=feature_id, enabled=bool(enabled), updated_by=int(user["id"]))
        db.add(flag)
    else:
        flag.enabled = bool(enabled)
        flag.updated_by = int(user["id"])
        flag.updated_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(url=f"/g/{guild_id}/features", status_code=303)
