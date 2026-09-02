#!/usr/bin/env bash
# .env를 읽어 서버 실행. 운영은 systemd 유닛 권장(README 참고).
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; source .env; set +a; fi
exec uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8080}"
