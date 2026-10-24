# services/api

FastAPI (Python 3.12) API 서버. 엔드포인트는 PRD §7.

- `GET /healthz` — 프로세스 생존
- `GET /v1/health` — DB(PostGIS)·Redis·OTP 연결 확인, 실패 시 503

설정은 환경 변수 `WHYUSE_DATABASE_URL`, `WHYUSE_REDIS_URL`, `WHYUSE_OTP_URL`.

```
uv run uvicorn whyuse_api.main:app --reload
```
