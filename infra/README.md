# infra

로컬 개발 스택 (PostgreSQL 16 + PostGIS, Redis, OpenTripPlanner 2, API).

```
docker compose -f infra/docker-compose.yml up -d --build --wait
infra/scripts/healthcheck.sh   # P0 완료 기준: DB·Redis·OTP·API
docker compose -f infra/docker-compose.yml down -v
```

포트는 `DB_PORT`·`REDIS_PORT`·`OTP_PORT`·`API_PORT`, OTP 힙은 `OTP_HEAP`(기본 1g)으로 바꾼다.
