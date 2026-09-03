"""기존 DB를 현재 schema.sql에 맞춰 재구성한다 (SQLite 공식 12단계 절차). 데이터 보존, 멱등.

사용:  서버 내려놓고  python migrate.py            (DB_PATH는 .env/환경변수에서 읽음)
       python migrate.py --dry-run               변경 내용만 출력

하는 일:
- schema.sql을 메모리 DB에서 실행해 '정답' 테이블 정의를 얻고, 실제 DB의 각 테이블을 그 정의로 새로 만든 뒤
  공통 컬럼을 복사해 교체한다 → 새 컬럼(기본값), 바뀐 CHECK/FK 규칙이 적용된다.
- 옵션 JSON의 옛 키 "delta" → "delta_price".
- 실행 전 <DB>.bak-<시각> 으로 백업(sqlite backup API). 실패하면 롤백되고 원본은 그대로.
"""
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app import config

SCHEMA = (Path(__file__).parent / "app" / "schema.sql").read_text(encoding="utf-8")
JSON_COLS = [("menus", "options"), ("survey_responses", "selected_options"), ("user_cafe_defaults", "selected_options")]


def cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def main(dry_run: bool):
    path = Path(config.DB_PATH)
    if not path.exists():
        sys.exit(f"DB가 없습니다: {path}  (새 설치는 seed.py만 실행하면 됩니다)")

    want = sqlite3.connect(":memory:")
    want.executescript(SCHEMA)
    tables = {r[0]: r[1] for r in want.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}

    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=OFF")
    plan = []
    for t, ddl in tables.items():
        old = cols(db, t)
        new = cols(want, t)
        if not old:
            plan.append((t, "새 테이블", new, ddl)); continue
        added, dropped = [c for c in new if c not in old], [c for c in old if c not in new]
        plan.append((t, f"재구성 (+{added} -{dropped})", [c for c in new if c in old], ddl))

    for t, what, common, _ in plan:
        print(f"- {t}: {what}")
    if dry_run:
        return

    bak = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S-%f}")
    with sqlite3.connect(bak) as b:
        db.backup(b)
    print(f"백업: {bak}")

    try:
        db.execute("BEGIN")
        for t, what, common, ddl in plan:
            if what == "새 테이블":
                db.execute(ddl); continue
            db.execute(re.sub(r"^CREATE TABLE (IF NOT EXISTS )?(\w+)", f"CREATE TABLE {t}__new", ddl, count=1))
            cl = ", ".join(common)
            db.execute(f"INSERT INTO {t}__new ({cl}) SELECT {cl} FROM {t}")
            db.execute(f"DROP TABLE {t}")
            db.execute(f"ALTER TABLE {t}__new RENAME TO {t}")
        for t, c in JSON_COLS:
            n = db.execute(f"UPDATE {t} SET {c} = REPLACE({c}, '\"delta\":', '\"delta_price\":') "
                           f"WHERE {c} LIKE '%\"delta\":%'").rowcount
            if n:
                print(f"- {t}.{c}: delta → delta_price {n}행")
        # 인덱스 재생성 (DROP TABLE로 함께 사라졌음). executescript는 트랜잭션을 끊으니 하나씩 execute
        for (sql,) in want.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
            db.execute(sql)
        bad = db.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"외래키 위반 {len(bad)}건: {bad[:5]}")
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        print("실패 — 롤백했습니다. 원본은 그대로이고 백업도 남아 있습니다.")
        raise
    db.execute("PRAGMA foreign_keys=ON")
    print(f"완료. 무결성: {db.execute('PRAGMA integrity_check').fetchone()[0]}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
