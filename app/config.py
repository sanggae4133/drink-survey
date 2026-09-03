"""환경 설정 — 환경변수 우선, 없으면 프로젝트 루트의 .env (.env.example 참고)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# .env를 앱이 직접 읽는다. run.sh/systemd 없이 uvicorn을 바로 띄워도 같은 설정이 들어가게.
# 이미 있는 환경변수(systemd EnvironmentFile, 테스트가 넣은 값)가 우선 — setdefault.
_env = BASE_DIR / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if _line.strip() and not _line.lstrip().startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "drink_survey.db"))
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")

# Google OAuth (GCP 콘솔 → 사용자 인증 정보 → OAuth 클라이언트 ID)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

# 회사 도메인 (예: company.co.kr). 비우면 도메인 검증 생략 — 개발용.
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "")

# 1이면 /login 화면에 dev 로그인 폼 노출 (구글 OAuth 없이 사전 등록 이메일로 로그인).
# 운영에서는 반드시 0.
DEV_LOGIN = os.environ.get("DEV_LOGIN", "0") == "1"

OAUTH_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# 텔레그램 알림 (선택). 봇 토큰이 없으면 알림 기능 전체가 꺼진다. chat_id는 그룹 관리에서 그룹별로.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# 알림 메시지에 붙일 링크의 기준 URL (예: https://drink.tailxxxx.ts.net). 비우면 링크 없이 보냄.
APP_URL = os.environ.get("APP_URL", "").rstrip("/")


def parse_minutes(raw: str) -> tuple:
    """'30,10' → (10, 30). '0'이면 () = 리마인더 끔. 잘못되면 ValueError."""
    mins = {int(x) for x in raw.split(",") if x.strip()} - {0}
    if any(m < 0 for m in mins) or "," in raw and not mins and raw.strip() != "0":
        raise ValueError
    return tuple(sorted(mins))

# 운영(DEV_LOGIN=0) 기동 가드. 기본 시크릿이면 누구나 admin 세션 쿠키를 서명해 만들 수 있다.
if not DEV_LOGIN:
    if SESSION_SECRET == "dev-secret-change-me":
        raise SystemExit("SESSION_SECRET이 기본값입니다. .env에 무작위 값을 넣으세요 "
                         "(python -c 'import secrets;print(secrets.token_hex(32))')")
    if not OAUTH_CONFIGURED:
        raise SystemExit("DEV_LOGIN=0인데 GOOGLE_CLIENT_ID/SECRET이 없어 아무도 로그인할 수 없습니다")
