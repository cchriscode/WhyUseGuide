# infra/otp

OpenTripPlanner 2 설정. compose가 이 폴더를 `/var/opentripplanner`로 마운트한다.

- `otp-config.json` — 헬스체크용 Actuator API 켬
- `build-config.json` — 그래프 입력. P0은 `sample/nippori-sample.osm.pbf`
- `router-config.json` — 라우팅 기본값

`sample/nippori-sample.osm`은 사람이 읽는 원본이고, OTP는 PBF만 읽으므로 고치면 다시 변환한다:

```
uv run --with osmium python -c "import osmium; w=osmium.SimpleWriter('infra/otp/sample/nippori-sample.osm.pbf', overwrite=True); [w.add(o) for o in osmium.FileProcessor('infra/otp/sample/nippori-sample.osm')]; w.close()"
```

P1에서 간토 OSM 추출본 + 도쿄 GTFS로 바꾼다 (`data/`는 git에 넣지 않음).
