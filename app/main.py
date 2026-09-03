"""앱 조립: 미들웨어, 라우터, 스키마 초기화, 1분 주기 스케줄러."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from starlette.middleware.sessions import SessionMiddleware

from . import config, services
from .db import init_db
from .routers import admin, auth, cafes, home, me, schedules, surveys

log = logging.getLogger(__name__)


async def _ticker():
    """기동 직후 1회, 이후 60초마다 services.tick(). 예외는 로그만 남기고 계속 돈다."""
    while True:
        try:
            await asyncio.to_thread(services.tick)  # sqlite·텔레그램은 동기라 스레드로
        except Exception:
            log.exception("tick 실패")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    init_db()
    task = asyncio.create_task(_ticker())
    yield
    task.cancel()


# 운영(DEV_LOGIN=0)에서는 /docs, /redoc, /openapi.json 전부 비활성 — 비로그인 공개 경로를 최소화
app = FastAPI(title="음료조사", lifespan=lifespan,
              openapi_url="/openapi.json" if config.DEV_LOGIN else None)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="lax",
    # Secure 플래그는 브라우저가 자기 연결(https, Funnel) 기준으로 판단하므로 운영에서는 True.
    # 개발(DEV_LOGIN=1)은 http://127.0.0.1 이라 False.
    https_only=not config.DEV_LOGIN,
)

MAX_BODY = 64 * 1024  # 폼 몇 칸짜리 앱. 옵션 JSON도 수 KB면 충분


@app.middleware("http")
async def _harden(request: Request, call_next):
    # ponytail: Content-Length만 본다. chunked 업로드는 안 잡히니 문제 되면 receive 래퍼로
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return PlainTextResponse("요청 본문이 너무 큽니다", status_code=413)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if not config.DEV_LOGIN:  # 브라우저↔Funnel 구간이 https라 유효
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(cafes.router)
app.include_router(surveys.router)
app.include_router(schedules.router)
app.include_router(me.router)
app.include_router(admin.router)
