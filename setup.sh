#!/usr/bin/env bash
# 최초 1회 실행: 비밀 키와 관리자 비밀번호를 만들어 .env 파일을 생성합니다.
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then
  echo ".env 파일이 이미 있습니다. 다시 만들려면 .env 를 지운 뒤 실행하세요."
  exit 0
fi
rand() { python3 -c "import secrets; print(secrets.token_urlsafe($1))" 2>/dev/null || openssl rand -base64 $1 | tr -d '/+=\n'; }
read -rp "관리자 아이디 [admin]: " ADMIN_ID
ADMIN_ID=${ADMIN_ID:-admin}
read -rsp "관리자 비밀번호 (8자 이상, 비우면 자동 생성): " ADMIN_PW; echo
if [ -z "$ADMIN_PW" ]; then ADMIN_PW=$(rand 9); GENERATED=1; fi
read -rp "웹 포트 [8000]: " PORT
PORT=${PORT:-8000}
sed -e "s|^SECRET_KEY=.*|SECRET_KEY=$(rand 48)|" \
    -e "s|^JUDGE_TOKEN=.*|JUDGE_TOKEN=$(rand 32)|" \
    -e "s|^ADMIN_ID=.*|ADMIN_ID=${ADMIN_ID}|" \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${ADMIN_PW}|" \
    -e "s|^WEB_PORT=.*|WEB_PORT=${PORT}|" .env.example > .env
chmod 600 .env
echo ".env 파일을 만들었습니다."
[ -n "$GENERATED" ] && echo "자동 생성된 관리자 비밀번호: ${ADMIN_PW}  (로그인 후 변경하세요)"
echo "다음 명령으로 실행하세요:  docker compose up -d --build"
