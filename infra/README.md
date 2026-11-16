# infra

로컬 개발용 docker compose: PostgreSQL 16 + PostGIS, Redis, OpenTripPlanner 2.10, API.

## 처음 한 번

```bash
cp .env.example .env              # 저장소 루트. 포트가 겹치면 *_PORT 값을 바꾼다
bash infra/otp/fetch-osm.sh       # 간토 OSM(Geofabrik, 약 520MB)을 받아 도쿄 23구 + 하네다·나리타 범위로 자름 (© OpenStreetMap contributors, ODbL)
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

## DB 스키마와 데이터

```bash
(cd services/api && uv run alembic upgrade head)   # 스키마 + 일본·도쿄 기본 행
uv run wug-data fetch                              # 원자료 내려받기 (ODPT 토큰은 .env의 ODPT_TOKEN)
uv run wug-data build-approx                       # 근사 노선 GTFS (approx_lines.toml)
uv run wug-data build-odpt-gtfs                    # 유리카모메: ODPT 역 시간표 → GTFS
uv run wug-data load-db                            # 역·노선 적재 (data/pipelines/README.md)
uv run wug-data load-fares                         # 도시별 근사 운임 (data/pipelines/city_fares.toml)
uv run wug-data load-places                        # 장소 후보 (Wikidata)
uv run wug-data load-place-reviews                 # 장소 검수 결과 반영 (data/pipelines/place_reviews/)
uv run wug-data stage-otp                          # GTFS를 OTP 입력으로 넣은 뒤 그래프 다시 빌드
```

DB 적재 테스트는 `TEST_DATABASE_URL`(예: `postgresql+psycopg://whyuseguide:whyuseguide@localhost:15432/whyuseguide`)이 있을 때 돈다. 테스트 데이터는 롤백된다.
