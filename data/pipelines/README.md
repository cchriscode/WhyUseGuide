# data/pipelines

교통·장소 데이터 수집 배치 (PRD §8). 무료이면서 상업 이용 가능한 출처만 쓴다(`docs/data-licenses.md`).

## 출처 목록

`sources.toml`이 PRD의 `data_sources` 설정이다. 출처마다 URL, 라이선스, 출처 표기 문구, 갱신 주기, OTP 입력 여부를 적는다. 새 출처는 라이선스를 `docs/data-licenses.md`에서 먼저 확인한 뒤 추가한다.

## 명령

```bash
uv run wug-data fetch [id ...]      # data/raw/<id>/ 에 받고 <파일>.provenance.json 에 받은 시각·SHA-256·라이선스 기록
uv run wug-data build-approx        # approx_lines.toml + 국토수치정보 N02 → 근사 GTFS (data/raw/approx-tokyo/)
uv run wug-data validate [id ...]   # GTFS 필수 파일·행 수·서비스 종료일 점검 (만료 30일 전부터 경고)
uv run wug-data stage-otp [id ...]  # 검증 통과한 GTFS를 infra/otp/<id>.gtfs.zip 으로 복사
uv run wug-data load-db [id ...]    # 역·노선을 DB에 적재하고 한국어 이름(names_ko.toml, 기계 변환)을 채움
```

`stage-otp` 뒤에는 OTP 그래프를 다시 빌드한다(`infra/README.md`).

## 근사 노선 (`approx_lines.toml`)

시간표를 상업적으로 쓸 수 없는 노선(JR·사철·공항선)은 요약 사실로 근사한다. 노선마다 역 순서(현지어·영어),
전 구간 소요 시간, 시간대별 배차 간격, 그리고 각 값의 근거(`facts`)를 적는다. 출처가 없는 값은 "추정"으로 적는다.

- 역 위치: 국토수치정보 N02에서 운영사 + 역 이름으로 찾는다(법적 노선명이 실제 운행 계통과 다르기 때문).
- 역 간 소요 시간: 전 구간 소요 시간을 역 간 거리 비율로 나눈다.
- 순환선: 출발점이 반대편인 두 패턴을 엇갈려 돌린다. 한 바퀴 시간은 시간대별로 배차 간격 두 배의 배수로 맞춘다
  (그래야 모든 역에서 같은 간격으로 열차가 지나간다).
- 이 노선은 `lines.estimated = true`로 적재되어 앱에서 "예상"으로 표시된다.
