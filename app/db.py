"""SQLite 연결·초기화. 모든 시간은 로컬 시간 문자열(README의 시간 규약 참고)."""
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config


def get_conn() -> sqlite3.Connection:
    # check_same_thread=False: FastAPI가 sync 의존성(스레드풀)과 async 엔드포인트(이벤트 루프)
    # 사이에서 같은 요청의 커넥션을 쓴다. 커넥션은 요청 단위로 격리되므로 안전.
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn = get_conn()
    try:
        conn.executescript(schema)
    finally:
        conn.close()


def db_dep():
    """FastAPI 의존성: 요청당 커넥션 하나."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_min() -> str:
    """분 단위 현재 시각 — deadline_at 비교용 ('YYYY-MM-DD HH:MM')."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
