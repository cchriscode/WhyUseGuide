# infra

로컬 개발용 docker compose: PostgreSQL 16 + PostGIS, Redis, OpenTripPlanner 2.10, API.

## 처음 한 번

```bash
cp .env.example .env              # 저장소 루트. 포트가 겹치면 *_PORT 값을 바꾼다
bash infra/otp/fetch-osm.sh       # 도쿄 OSM 추출본(약 90MB, © OpenStreetMap contributors, ODbL)
docker compose -f infra/docker-compose.yml --env-file .env --profile build run --rm otp-build   # 그래프 빌드, 약 7분
```

## 실행·확인

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d --build --wait
curl http://localhost:8000/health   # db·redis·otp 모두 ok면 200
```

| 서비스 | 호스트 포트 (기본) |
| --- | --- |
| db | 15432 |
| redis | 16379 |
| otp | 18080 |
| api | 8000 |

P0 그래프는 도쿄 OSM만 담는다(도보·자동차). 대중교통 GTFS는 P1에서 더한다(`docs/data-licenses.md`).
