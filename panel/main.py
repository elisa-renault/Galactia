# /home/Galactia/panel/main.py
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates
from core.settings import settings

app = FastAPI(title=settings.APP_TITLE)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    https_only=(settings.ENV != "dev"),  # True en prod
    same_site="lax",                     # "strict" si pas d’OAuth cross-site
    session_cookie="galactia_session",
    max_age=60*60*8                      # 8h
)

app.mount("/static", StaticFiles(directory="panel/static"), name="static")
templates = Jinja2Templates(directory="panel/templates")

# Routes de base (login/logout dans auth)
from panel.routers import me, guilds, settings as rsettings, features as rfeatures, admin as radmin
app.include_router(me.router, prefix="", tags=["me"])
app.include_router(guilds.router, prefix="", tags=["guilds"])
app.include_router(rsettings.router, prefix="", tags=["settings"])
app.include_router(rfeatures.router)
app.include_router(radmin.router)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse("home.html", {"request": request, "user": user})
