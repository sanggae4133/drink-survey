"""내 설정: 카페별 즐겨찾기 관리 (등록은 조사 응답 화면의 체크박스로도 가능)."""
import json
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import models
from ..db import db_dep
from ..deps import current_user, flash, render

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
def me_page(request: Request, user: sqlite3.Row = Depends(current_user),
            db: sqlite3.Connection = Depends(db_dep)):
    favs = db.execute(
        "SELECT d.*, c.name AS cafe_name, m.name AS menu_name "
        "FROM user_cafe_defaults d JOIN cafes c ON c.id=d.cafe_id JOIN menus m ON m.id=d.menu_id "
        "WHERE d.user_id=? ORDER BY c.name", (user["id"],)).fetchall()
    items = [{"row": f, "label": models.item_label(f["menu_name"], json.loads(f["selected_options"]))}
             for f in favs]
    return render(request, "me.html", user=user, favs=items)


@router.post("/defaults/{cafe_id}/delete")
def delete_default(cafe_id: int, request: Request, user: sqlite3.Row = Depends(current_user),
                   db: sqlite3.Connection = Depends(db_dep)):
    with db:
        db.execute("DELETE FROM user_cafe_defaults WHERE user_id=? AND cafe_id=?",
                   (user["id"], cafe_id))
    flash(request, "즐겨찾기를 삭제했습니다")
    return RedirectResponse("/me", status_code=303)
