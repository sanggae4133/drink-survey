"""앱 조립: 미들웨어, 라우터, 예외 핸들러, 스키마 초기화."""
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import init_db
from .routers import admin, auth, cafes, home, me, schedules, surveys

# 운영(DEV_LOGIN=0)에서는 /docs, /redoc, /openapi.json 전부 비활성 — 비로그인 공개 경로를 최소화
app = FastAPI(title="음료조사", openapi_url="/openapi.json" if config.DEV_LOGIN else None)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="lax",
    # Secure 플래그는 브라우저가 자기 연결(https, Funnel) 기준으로 판단하므로 운영에서는 True.
    # 개발(DEV_LOGIN=1)은 http://127.0.0.1 이라 False.
    https_only=not config.DEV_LOGIN,
)

init_db()

app.include_router(auth.router)
app.include_router(home.router)
app.include_router(cafes.router)
app.include_router(surveys.router)
app.include_router(schedules.router)
app.include_router(me.router)
app.include_router(admin.router)
