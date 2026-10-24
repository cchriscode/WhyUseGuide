# 결정 기록

PRD를 구현하면서 정하거나 바꾼 결정을 날짜와 함께 남긴다 (PRD §11 작업 규칙).

## 2026-10-24 · P0 기반 세팅

- **OTP 버전 고정:** `opentripplanner/opentripplanner:2.6.0`. 업그레이드는 그래프 재빌드와 함께 한다.
- **P0 OTP 그래프:** 실제 데이터(P1) 전까지 `infra/otp/sample/`의 손으로 만든 작은 보행 네트워크(닛포리역 주변)로 기동 시 `--build --serve` 한다. 헬스체크 통과용이며 경로 품질과 무관하다. P1에서 간토 OSM + 도쿄 GTFS 빌드 잡을 분리하고 `--load`로 바꾼다.
- **로컬 OTP 힙:** 기본 1GB(`OTP_HEAP`). 실 그래프는 PRD §1대로 8GB 이상.
- **헬스체크 엔드포인트:** `GET /healthz`(프로세스 생존), `GET /v1/health`(DB의 PostGIS·Redis·OTP `/otp/actuators/health` 확인, 하나라도 실패하면 503). P0 완료 기준은 `infra/scripts/healthcheck.sh`가 `/v1/health` 200을 받는 것.
- **Python 워크스페이스:** 루트 `pyproject.toml`의 uv 워크스페이스에 `services/api`(`whyuse_api`), `services/engine`(`itinerary_engine`). 린트 ruff, 타입 mypy strict, 테스트 pytest.
- **JS 워크스페이스:** pnpm (`apps/*`, `packages/*`), ESLint 9 + typescript-eslint, Jest(ts-jest). `apps/mobile`, `packages/engine-ts`는 각각 P4·P2에서 패키지로 만든다.
