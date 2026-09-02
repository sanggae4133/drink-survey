"""앱 조립: 미들웨어, 라우터, 예외 핸들러, 스키마 초기화."""
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import init_db
from .routers import admin, auth, cafes, home, me, schedules, surveys

app = FastAPI(title="음료조사", docs_url="/docs")

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="lax",
    https_only=False,  # Funnel이 TLS 종단이라 앱까지는 http로 온다
)

init_db()

app.include_router(auth.router)
app.include_router(home.router)
app.include_router(cafes.router)
app.include_router(surveys.router)
app.include_router(schedules.router)
app.include_router(me.router)
app.include_router(admin.router)
