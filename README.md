# WhyUseGuide

취향·일수·인원을 넣으면 실제 교통 데이터로 동선과 비용을 최적화한 여행 일정을 짜 주고, 여행 중에도 일정이 바뀌면 자동으로 다시 계산해 주는 여행 플래너 앱.

- 상세 요구사항: [docs/PRD.md](docs/PRD.md)
- 스택: Expo (React Native, TypeScript) · FastAPI · PostgreSQL + PostGIS · Redis · OpenTripPlanner 2 · OR-Tools · MapLibre

## 구조

| 경로 | 역할 |
|---|---|
| `apps/mobile` | Expo 앱 (iOS · Android) |
| `apps/web` | Next.js 웹 (2단계) |
| `services/api` | FastAPI 서버 |
| `services/engine` | 서버측 일정·경로 엔진 |
| `packages/engine-ts` | 기기 재계산 엔진 |
| `packages/ui-tokens` | 디자인 토큰 |
| `data/pipelines` | 교통·장소 데이터 수집 |
| `infra` | 배포 |
| `docs` | PRD |

개발 단계(P0–P6)는 PRD §11을 따릅니다. 결정 기록은 [docs/decisions.md](docs/decisions.md).

## 개발 환경

Node 22 + pnpm 10, Python 3.12 + uv, Docker.

```
pnpm install && uv sync

pnpm lint && pnpm typecheck && pnpm test          # JS
uv run ruff check . && uv run mypy && uv run pytest # Python

docker compose -f infra/docker-compose.yml up -d --build --wait
infra/scripts/healthcheck.sh
```
