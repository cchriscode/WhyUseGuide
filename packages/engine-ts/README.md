# packages/engine-ts

기기에서 도는 결정론적 재계산 엔진 (compute / autoAdjust / suggest). generate·planTrip은 서버 전용(PRD §11-3, §13.4). 상수와 점수식은 PRD §5, 상수 파일은 `packages/engine-config/constants.json`(Python 엔진과 공유).

Python 엔진(`services/engine`)과 결과가 같아야 한다. `/engine-fixtures/*.json`을 두 구현이 같이 돌린다(`pnpm test`, `uv run pytest services/engine`). 엔진 동작을 바꾸면 Python을 먼저 고치고 `uv run python services/engine/scripts/make_fixtures.py`로 픽스처를 다시 만든 뒤 TS를 맞춘다.
