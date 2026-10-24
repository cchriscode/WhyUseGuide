#!/usr/bin/env bash
# PRD §11 P0 완료 기준: docker compose로 DB·OTP·API 헬스체크 통과.
# API의 /v1/health가 DB(PostGIS)·Redis·OTP를 모두 확인하므로 이 응답이 200이면 통과다.
set -euo pipefail

API_URL="${API_URL:-http://localhost:${API_PORT:-8000}}"
TRIES="${TRIES:-30}"

for ((i = 1; i <= TRIES; i++)); do
  if body="$(curl -sf "$API_URL/v1/health")"; then
    echo "$body"
    echo "OK: DB·Redis·OTP·API 헬스체크 통과"
    exit 0
  fi
  sleep 2
done

echo "FAIL: $API_URL/v1/health 가 200을 돌려주지 않았어요" >&2
curl -s "$API_URL/v1/health" >&2 || true
exit 1
