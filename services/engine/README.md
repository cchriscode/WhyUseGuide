# services/engine

일정 엔진 `itinerary_engine` (PRD §5). 입력이 같으면 결과가 같은 순수 함수.

| 모듈 | 내용 |
| --- | --- |
| `route` | §5.3 구간 경로(알려진 구간 우선, 없으면 근사) |
| `compute` | §5.1 시각표 계산, §13.4 숙소·자동차 |
| `adjust` | §5.4 자동 조정 |
| `suggest` | §5.5 대체·사이 들르기·남는 시간 |
| `generate` | §5.2 일정 생성(OR-Tools), 서버 전용 |
| `feedback` | §5.6 코스 평가 반영 |

compute·adjust·suggest는 `packages/engine-ts`에 같은 로직이 있고 `/engine-fixtures`로 결과 일치를 확인한다.
