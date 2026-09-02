"""카페·메뉴: 누구나 등록/수정. 메뉴 수정은 updated_by/updated_at 기록."""
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import models
from ..db import db_dep, now_str
from ..deps import current_user, flash, render

router = APIRouter(tags=["cafes"])


def _url(raw: str) -> str | None:
    """빈 값은 None. http(s)만 허용 — 'javascript:' 링크를 다른 사용자가 클릭하게 만드는 XSS 차단."""
    u = raw.strip()
    if u and not u.startswith(("http://", "https://")):
        raise ValueError("메뉴판 링크는 http:// 또는 https:// 로 시작해야 합니다")
    return u or None


@router.get("/cafes")
def cafes_list(request: Request, user: sqlite3.Row = Depends(current_user),
               db: sqlite3.Connection = Depends(db_dep)):
    cafes = db.execute(
        "SELECT c.*, u.name AS creator_name, "
        "  (SELECT COUNT(*) FROM menus m WHERE m.cafe_id=c.id AND m.is_active=1) AS menu_count "
        "FROM cafes c JOIN users u ON u.id=c.created_by "
        "WHERE c.is_active=1 ORDER BY c.name").fetchall()
    return render(request, "cafes.html", user=user, cafes=cafes)


@router.post("/cafes")
def cafe_create(request: Request, name: str = Form(...), menu_url: str = Form(""),
                user: sqlite3.Row = Depends(current_user),
                db: sqlite3.Connection = Depends(db_dep)):
    try:
        url = _url(menu_url)
    except ValueError as e:
        flash(request, str(e))
        return RedirectResponse("/cafes", status_code=303)
    with db:
        cur = db.execute(
            "INSERT INTO cafes (name, menu_url, created_by) VALUES (?,?,?)",
            (name.strip(), url, user["id"]))
    return RedirectResponse(f"/cafes/{cur.lastrowid}", status_code=303)


@router.get("/cafes/{cid}")
def cafe_detail(cid: int, request: Request, user: sqlite3.Row = Depends(current_user),
                db: sqlite3.Connection = Depends(db_dep)):
    cafe = db.execute("SELECT * FROM cafes WHERE id=?", (cid,)).fetchone()
    if cafe is None:
        raise HTTPException(404)
    menus = db.execute(
        "SELECT m.*, u.name AS updater_name FROM menus m "
        "LEFT JOIN users u ON u.id=m.updated_by "
        "WHERE m.cafe_id=? ORDER BY m.is_active DESC, m.name", (cid,)).fetchall()
    return render(request, "cafe_detail.html", user=user, cafe=cafe, menus=menus,
                  options_example=models.OPTIONS_EXAMPLE)


@router.post("/cafes/{cid}")
def cafe_update(cid: int, request: Request, name: str = Form(...), menu_url: str = Form(""),
                default_menu_id: str = Form(""),
                user: sqlite3.Row = Depends(current_user),
                db: sqlite3.Connection = Depends(db_dep)):
    dmi = int(default_menu_id) if default_menu_id else None
    try:
        url = _url(menu_url)
    except ValueError as e:
        flash(request, str(e))
        return RedirectResponse(f"/cafes/{cid}", status_code=303)
    if dmi is not None:
        ok = db.execute("SELECT 1 FROM menus WHERE id=? AND cafe_id=? AND is_active=1",
                        (dmi, cid)).fetchone()
        if not ok:
            flash(request, "기본음료는 이 카페의 판매 중 메뉴여야 합니다")
            return RedirectResponse(f"/cafes/{cid}", status_code=303)
    with db:
        db.execute("UPDATE cafes SET name=?, menu_url=?, default_menu_id=? WHERE id=?",
                   (name.strip(), url, dmi, cid))
    flash(request, "카페 정보를 저장했습니다")
    return RedirectResponse(f"/cafes/{cid}", status_code=303)


@router.post("/cafes/{cid}/menus")
def menu_create(cid: int, request: Request, name: str = Form(...),
                base_price: int = Form(...), options: str = Form("[]"),
                user: sqlite3.Row = Depends(current_user),
                db: sqlite3.Connection = Depends(db_dep)):
    try:
        models.parse_option_groups(options)
    except ValueError as e:
        flash(request, str(e))
        return RedirectResponse(f"/cafes/{cid}", status_code=303)
    with db:
        db.execute(
            "INSERT INTO menus (cafe_id, name, base_price, options, created_by) VALUES (?,?,?,?,?)",
            (cid, name.strip(), base_price, options.strip() or "[]", user["id"]))
    flash(request, f"메뉴 추가됨: {name}")
    return RedirectResponse(f"/cafes/{cid}", status_code=303)


@router.post("/menus/{mid}")
def menu_update(mid: int, request: Request, name: str = Form(...),
                base_price: int = Form(...), options: str = Form("[]"),
                is_active: str = Form(""),
                user: sqlite3.Row = Depends(current_user),
                db: sqlite3.Connection = Depends(db_dep)):
    menu = db.execute("SELECT * FROM menus WHERE id=?", (mid,)).fetchone()
    if menu is None:
        raise HTTPException(404)
    try:
        models.parse_option_groups(options)
    except ValueError as e:
        flash(request, str(e))
        return RedirectResponse(f"/cafes/{menu['cafe_id']}", status_code=303)
    with db:
        db.execute(
            "UPDATE menus SET name=?, base_price=?, options=?, is_active=?, updated_by=?, updated_at=? "
            "WHERE id=?",
            (name.strip(), base_price, options.strip() or "[]",
             1 if is_active else 0, user["id"], now_str(), mid))
    flash(request, f"메뉴 수정됨: {name}")
    return RedirectResponse(f"/cafes/{menu['cafe_id']}", status_code=303)
