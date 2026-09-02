"""앱 조립: 미들웨어, 라우터, 예외 핸들러, 스키마 초기화."""
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import init_db
from .deps import LoginRequired
from .routers import admin, auth, cafes, home, me, schedules, surveys

app = FastAPI(title="음료조사", docs_url="/docs")

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="lax",
    https_only=False,  # Funnel이 TLS 종단이라 앱까지는 http로 온다
)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.on_event("startup")
def _startup():
    init_db()


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(cafes.router)
app.include_router(surveys.router)
app.include_router(schedules.router)
app.include_router(me.router)
app.include_router(admin.router)
