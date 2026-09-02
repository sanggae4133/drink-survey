"""환경 설정 — 전부 환경변수로 주입한다 (.env.example 참고)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

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
