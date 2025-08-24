# /home/Galactia/panel/routers/me.py
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from panel.auth.discord import login_redirect, handle_callback
from core.db import get_db
from sqlalchemy.orm import Session
from fastapi import Depends
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="panel/templates")
router = APIRouter()

@router.get("/login")
def login(request: Request):
    return login_redirect(request)

@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    return await handle_callback(request, db)

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")


