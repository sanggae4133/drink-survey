"""관리자: 회원 사전 등록·관리, 그룹 트리·멤버 관리."""
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import services
from ..db import db_dep
from ..deps import flash, render, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


# ------------------------------------------------------------------ 회원

@router.get("/users")
def users_page(request: Request, user: sqlite3.Row = Depends(require_admin),
               db: sqlite3.Connection = Depends(db_dep)):
    users = db.execute("SELECT * FROM users ORDER BY status DESC, name").fetchall()
    return render(request, "admin_users.html", user=user, users=users)


@router.post("/users")
def user_create(request: Request, email: str = Form(...), name: str = Form(...),
                role: str = Form("member"),
                user: sqlite3.Row = Depends(require_admin),
                db: sqlite3.Connection = Depends(db_dep)):
    email = email.strip().lower()
    if role not in ("member", "admin"):
        raise HTTPException(400)
    dup = db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone()
    if dup:
        flash(request, "이미 등록된 이메일입니다")
    else:
        with db:
            db.execute("INSERT INTO users (email, name, role) VALUES (?,?,?)",
                       (email, name.strip(), role))
        flash(request, f"{name} 님을 등록했습니다. 본인이 구글 로그인하면 계정이 연결됩니다")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{uid}")
def user_update(uid: int, request: Request, name: str = Form(...),
                email: str = Form(...), role: str = Form(...),
                user: sqlite3.Row = Depends(require_admin),
                db: sqlite3.Connection = Depends(db_dep)):
    target = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if target is None or role not in ("member", "admin"):
        raise HTTPException(400)
    email = email.strip().lower()
    if target["status"] != "invited" and email != target["email"]:
        flash(request, "이미 연결된 계정의 이메일은 바꿀 수 없습니다")
        return RedirectResponse("/admin/users", status_code=303)
    if uid == user["id"] and role != "admin":
        flash(request, "자기 자신의 관리자 권한은 뺄 수 없습니다 (잠금 방지)")
        return RedirectResponse("/admin/users", status_code=303)
    with db:
        db.execute("UPDATE users SET name=?, email=?, role=? WHERE id=?",
                   (name.strip(), email, role, uid))
    flash(request, "저장했습니다")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{uid}/toggle")
def user_toggle(uid: int, request: Request, user: sqlite3.Row = Depends(require_admin),
                db: sqlite3.Connection = Depends(db_dep)):
    """비활성화 ↔ 복구. 복구 시 구글 연결이 없던 계정은 invited로 돌아간다."""
    target = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if target is None or uid == user["id"]:
        raise HTTPException(400)
    if target["status"] == "disabled":
        new = "active" if target["google_sub"] else "invited"
    else:
        new = "disabled"
    with db:
        db.execute("UPDATE users SET status=? WHERE id=?", (new, uid))
    flash(request, "계정을 " + ("비활성화했습니다. 다음 요청부터 로그아웃됩니다" if new == "disabled" else "복구했습니다"))
    return RedirectResponse("/admin/users", status_code=303)


# ------------------------------------------------------------------ 그룹

@router.get("/groups")
def groups_page(request: Request, user: sqlite3.Row = Depends(require_admin),
                db: sqlite3.Connection = Depends(db_dep)):
    tree = services.group_tree(db)
    groups = [t["row"] for t in tree]
    users = db.execute("SELECT * FROM users ORDER BY name").fetchall()
    member_map = {
        g["id"]: {r["user_id"] for r in db.execute(
            "SELECT user_id FROM group_members WHERE group_id=?", (g["id"],))}
        for g in groups
    }
    eff_count = {g["id"]: len(services.effective_members(db, g["id"])) for g in groups}
    return render(request, "admin_groups.html", user=user, tree=tree, groups=groups,
                  users=users, member_map=member_map, eff_count=eff_count)


@router.post("/groups")
def group_create(request: Request, name: str = Form(...), parent_group_id: str = Form(""),
                 user: sqlite3.Row = Depends(require_admin),
                 db: sqlite3.Connection = Depends(db_dep)):
    parent = int(parent_group_id) if parent_group_id else None
    try:
        with db:
            db.execute("INSERT INTO groups (name, parent_group_id) VALUES (?,?)",
                       (name.strip(), parent))
    except sqlite3.IntegrityError:
        flash(request, "이미 있는 그룹 이름입니다")
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/groups/{gid}")
def group_update(gid: int, request: Request, name: str = Form(...),
                 parent_group_id: str = Form(""), telegram_chat_id: str = Form(""),
                 user: sqlite3.Row = Depends(require_admin),
                 db: sqlite3.Connection = Depends(db_dep)):
    parent = int(parent_group_id) if parent_group_id else None
    if services.creates_cycle(db, gid, parent):
        flash(request, "순환 구조는 만들 수 없습니다 (자기 자신/하위 그룹을 상위로 지정 불가)")
        return RedirectResponse("/admin/groups", status_code=303)
    with db:
        db.execute("UPDATE groups SET name=?, parent_group_id=?, telegram_chat_id=? WHERE id=?",
                   (name.strip(), parent, telegram_chat_id.strip() or None, gid))
    flash(request, "저장했습니다")
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/groups/{gid}/members")
async def group_members_update(gid: int, request: Request,
                               user: sqlite3.Row = Depends(require_admin),
                               db: sqlite3.Connection = Depends(db_dep)):
    form = await request.form()
    ids = [int(v) for v in form.getlist("member_ids")]
    with db:
        db.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
        db.executemany("INSERT INTO group_members (group_id, user_id) VALUES (?,?)",
                       [(gid, uid) for uid in ids])
    flash(request, "멤버를 저장했습니다")
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/groups/{gid}/delete")
def group_delete(gid: int, request: Request, user: sqlite3.Row = Depends(require_admin),
                 db: sqlite3.Connection = Depends(db_dep)):
    try:  # 하위 그룹·조사·스케줄의 FK가 막는다 (PRAGMA foreign_keys=ON)
        with db:
            db.execute("DELETE FROM groups WHERE id=?", (gid,))
        flash(request, "그룹을 삭제했습니다")
    except sqlite3.IntegrityError:
        flash(request, "하위 그룹·조사·스케줄이 있는 그룹은 삭제할 수 없습니다")
    return RedirectResponse("/admin/groups", status_code=303)
