"""공통 의존성: 로그인 사용자, 관리자 요구, 템플릿 렌더 헬퍼."""
import sqlite3
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from .db import db_dep

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class LoginRequired(Exception):
    """main.py의 핸들러가 /login으로 리다이렉트한다."""


def current_user(request: Request, db: sqlite3.Connection = Depends(db_dep)) -> sqlite3.Row:
    uid = request.session.get("user_id")
    if not uid:
        raise LoginRequired()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if user is None:
        request.session.clear()
        raise LoginRequired()
    return user


def require_admin(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="관리자만 가능합니다")
    return user


def flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def render(request: Request, name: str, **ctx):
    """flash 메시지를 꺼내 넣는 공통 렌더."""
    ctx.setdefault("flash", request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, ctx)
