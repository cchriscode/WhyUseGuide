# 작업 안내

- 요구사항의 원본은 `docs/PRD.md`입니다. 화면 ID(S-xx), 기능 ID(F-xx)를 커밋·PR 제목에 적어 주세요.
- 일정의 시간·요금·순서는 결정론적 엔진(`packages/engine-ts`, `services/engine`)이 계산합니다. LLM은 장소 태깅·팁 생성 배치에만 씁니다.
- 엔진 상수와 점수식은 PRD §5 값을 그대로 쓰고, 바꿀 때는 PRD도 함께 고칩니다.
- 확정 못 한 교통 정보는 '예상' 배지로 구분합니다. 나라별 데이터 등급(precise/basic/reference)은 PRD §13.
- `creator_courses`(유튜버 코스)는 기능 플래그 뒤에 둡니다. 출시 전 법률 검토 대상.

# 코드 작성 규칙

코드를 쓰기 전에 읽고, 끝내기 전에 자기 변경을 이 규칙으로 다시 점검합니다. 어긋난 곳을 발견하면 같이 고치거나, 범위 밖이면 알립니다.

1. **패턴을 지킨다.** 새로 만들기 전에 같은 일을 하는 기존 코드를 찾아 그 방식을 따릅니다.
   - API: 라우터(`routers/`)는 입력 검증과 엔드포인트만, 일정 읽기·쓰기·응답 모양은 `itinerary.py`, 입력 모델은 `schemas.ApiModel`, 오류는 `ApiError`, 요청마다 필요한 것은 `deps.py`의 `Annotated` 의존성으로 받습니다.
   - 엔진: Python과 TS가 같은 규칙·같은 반올림. 한쪽을 고치면 다른 쪽과 공통 픽스처도 같이 고칩니다.
2. **한 곳에만 둔다(One Source of Truth).** 같은 값·규칙·SQL·질의문을 두 군데에 적지 않습니다. 이미 있는 곳:

   | 무엇 | 어디 |
   | --- | --- |
   | 엔진 상수(PRD §5) | `packages/engine-config/constants.json` (+ `services/engine` 안 사본, 테스트가 일치 확인) |
   | OTP 질의문·응답 해석 | `services/api/src/whyuseguide_api/otp.py` |
   | 노선망 SQL(환승 가능 노선, 가까운 역) | `whyuseguide_api/network.py` |
   | 데이터 출처·라이선스 | `data/pipelines/sources.toml` |
   | 도시별 근사 운임(출처·확인일 포함) | `data/pipelines/city_fares.toml` → `wug-data load-fares` |
   | 근사 노선과 그 지역 값(영역, 시간대, 원자료 출처) | `data/pipelines/approx_lines.toml` |
   | 장소 유형·식사 유형, 평가 반영 수치 | `constants.json`의 `placeTypes`·`mealTypes`·`feedback` |
   | 평가 태그 코드 | `itinerary_engine.feedback.RatingTag` |
   | API 응답 모양 | `whyuseguide_api/itinerary.py`의 응답 모델(`DayOut` 등) |
   | 테스트 입력 도우미·픽스처 위치 | 엔진 `itinerary_engine/fixtures.py`, 파이프라인 `tests/support.py`, API `tests/conftest.py` |
   | 접속 주소·시간 제한 등 실행 설정 | `whyuseguide_api/config.py` `Settings` (저장소 루트 `.env`) |
   | 도시별 값(시간대, 운임표) | DB `cities` |

   API와 파이프라인이 같이 쓰는 코드는 `whyuseguide_api`에 두고 파이프라인이 가져다 씁니다.
3. **중요한 값을 하드코딩하지 않는다.** 임계값·거리·날짜·시간대·주소·동시 실행 수·도시별 값은 위 표의 자리에 둡니다. 코드에 둘 수밖에 없는 조정값은 이름 있는 상수로 만들고 왜 그 값인지 주석을 답니다. 날짜와 특정 도시의 값(위도, 시간대)은 코드에 적지 않습니다.
4. **오류를 조용히 넘기지 않는다.**
   - 외부 호출(OTP, 내려받기)은 시간 제한을 두고, 실패하면 `logging.getLogger(__name__)`으로 남긴 뒤 대체 동작을 하거나 멈춥니다.
   - `except`는 구체적인 예외만 잡습니다. 잡았으면 기록하거나 다시 던집니다.
   - 숫자·요금·날짜처럼 바깥 세상의 값은 기억이 아니라 공식 자료로 확인하고, 출처와 확인일을 값 옆에 적습니다(택시 운임이 개정 전 값으로 박혀 있었습니다).
   - API 응답은 `errors.py` 형식만 씁니다. 내부 예외 문구(주소·SQL)는 응답에 싣지 않고 로그에만 남깁니다.
   - 값이 없을 때 0이나 빈 값으로 계산이 계속되면 틀린 결과가 그대로 나갑니다. 그런 자리에는 검증이나 경고를 둡니다.
   - 실패 경로 테스트는 기대한 이유로 통과하는지 확인합니다(예: 요청이 실제로 나갔는지).
5. **한 가지 책임(Single Responsibility).** 함수와 모듈은 한 가지 일만 합니다. 계산 규칙은 입출력과 분리해 순수 함수로 둡니다(예: `timed.pick_itinerary`). 함수가 검증·계산·저장·응답 만들기를 다 하고 있으면 나눕니다.
6. **공유 파일·폴더를 정해진 자리에 둔다.** 공유 상수는 `packages/engine-config`, 두 엔진의 공통 테스트 입력은 `engine-fixtures/`(`services/engine/scripts/make_fixtures.py`로만 만듭니다), 내려받은 원자료와 벤치마크 결과는 `data/raw`(커밋하지 않음). 일회용 스크립트와 결과물은 저장소에 두지 않습니다.

끝내기 전 확인: `uv run ruff check . && uv run ruff format --check .`, mypy(CI와 같은 범위), `uv run pytest`(DB 테스트는 `TEST_DATABASE_URL`을 테스트 DB로), `packages/engine-ts`의 jest·tsc, 루트 eslint.

