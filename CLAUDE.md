# 작업 안내

- 요구사항의 원본은 `docs/PRD.md`입니다. 화면 ID(S-xx), 기능 ID(F-xx)를 커밋·PR 제목에 적어 주세요.
- 일정의 시간·요금·순서는 결정론적 엔진(`packages/engine-ts`, `services/engine`)이 계산합니다. LLM은 장소 태깅·팁 생성 배치에만 씁니다.
- 엔진 상수와 점수식은 PRD §5 값을 그대로 쓰고, 바꿀 때는 PRD도 함께 고칩니다.
- 확정 못 한 교통 정보는 '예상' 배지로 구분합니다. 나라별 데이터 등급(precise/basic/reference)은 PRD §13.
- `creator_courses`(유튜버 코스)는 기능 플래그 뒤에 둡니다. 출시 전 법률 검토 대상.
