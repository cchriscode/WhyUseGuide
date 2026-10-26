# data/pipelines

교통·장소 데이터 수집 배치 (PRD §8). 무료이면서 상업 이용 가능한 출처만 쓴다(`docs/data-licenses.md`).

## 출처 목록

`sources.toml`이 PRD의 `data_sources` 설정이다. 출처마다 URL, 라이선스, 출처 표기 문구, 갱신 주기, OTP 입력 여부를 적는다. 새 출처는 라이선스를 `docs/data-licenses.md`에서 먼저 확인한 뒤 추가한다.

## 명령

```bash
uv run wug-data fetch [id ...]      # data/raw/<id>/ 에 받고 <파일>.provenance.json 에 받은 시각·SHA-256·라이선스 기록
uv run wug-data validate [id ...]   # GTFS 필수 파일·행 수·서비스 종료일 점검 (만료 30일 전부터 경고)
uv run wug-data stage-otp [id ...]  # 검증 통과한 GTFS를 infra/otp/<id>.gtfs.zip 으로 복사
```

`stage-otp` 뒤에는 OTP 그래프를 다시 빌드한다(`infra/README.md`).
