"""인증: 구글 OAuth(사전 등록 이메일에 sub 바인딩) + 개발용 dev 로그인.

콜백 검증 순서(설계서):
① 이메일 도메인 검증 → ② email로 사전 등록 행 조회(없으면 거절)
→ ③ google_sub 바인딩 + status=active.
이미 바인딩된 행에 다른 sub가 오면 거절(계정 가로채기 방지).
"""
import sqlite3

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from .. import config
from ..db import db_dep
from ..deps import flash, render

router = APIRouter(tags=["auth"])

oauth = None
if config.OAUTH_CONFIGURED:
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        "google",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
        authorize_params={"hd": config.ALLOWED_DOMAIN} if config.ALLOWED_DOMAIN else None,
    )


@router.get("/login")
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return render(
        request, "login.html",
        user=None, dev_login=config.DEV_LOGIN, oauth_ready=config.OAUTH_CONFIGURED,
    )


@router.get("/auth/google")
async def google_login(request: Request):
    if not oauth:
        flash(request, "구글 OAuth가 설정되지 않았습니다 (.env 확인)")
        return RedirectResponse("/login", status_code=303)
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def google_callback(request: Request, db: sqlite3.Connection = Depends(db_dep)):
    if not oauth:
        return RedirectResponse("/login", status_code=303)
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:  # state 불일치, 사용자 취소, 만료된 code 등 — 500 대신 로그인 화면으로
        flash(request, "구글 로그인에 실패했습니다. 다시 시도해 주세요")
        return RedirectResponse("/login", status_code=303)
    info = token.get("userinfo") or {}
    sub, email = info.get("sub"), (info.get("email") or "").lower()
    name = info.get("name") or email

    if not sub or not email or not info.get("email_verified", False):
        flash(request, "구글 계정 정보를 확인할 수 없습니다")
        return RedirectResponse("/login", status_code=303)
    if config.ALLOWED_DOMAIN and not email.endswith("@" + config.ALLOWED_DOMAIN):
        flash(request, f"회사 계정({config.ALLOWED_DOMAIN})으로만 로그인할 수 있습니다")
        return RedirectResponse("/login", status_code=303)

    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if user is None or user["status"] == "disabled":
        flash(request, "등록되지 않았거나 비활성화된 계정입니다. 관리자에게 문의하세요")
        return RedirectResponse("/login", status_code=303)
    if user["google_sub"] is None:
        with db:
            db.execute(
                "UPDATE users SET google_sub=?, status='active', name=? WHERE id=? AND google_sub IS NULL",
                (sub, name, user["id"]),
            )
    elif user["google_sub"] != sub:
        flash(request, "이 이메일은 다른 구글 계정에 이미 연결되어 있습니다. 관리자에게 문의하세요")
        return RedirectResponse("/login", status_code=303)

    request.session["user_id"] = user["id"]
    return RedirectResponse("/", status_code=303)


@router.post("/auth/dev")
def dev_login(request: Request, email: str = Form(...), db: sqlite3.Connection = Depends(db_dep)):
    """DEV_LOGIN=1일 때만. 사전 등록된 이메일로 즉시 로그인(바인딩 시뮬레이션)."""
    if not config.DEV_LOGIN:
        return RedirectResponse("/login", status_code=303)
    user = db.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if user is None or user["status"] == "disabled":
        flash(request, "등록되지 않았거나 비활성화된 계정입니다. 관리자에게 문의하세요")
        return RedirectResponse("/login", status_code=303)
    if user["status"] == "invited":
        with db:
            db.execute("UPDATE users SET status='active' WHERE id=?", (user["id"],))
    request.session["user_id"] = user["id"]
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
