# 데이터 출처·라이선스 점검표

2026-10-26 조사. MVP는 **무료이면서 상업 이용이 가능한 데이터만** 쓴다(decisions.md 2026-10-26). 라이선스는 바뀔 수 있으므로 출시 전과 데이터를 새로 받을 때 다시 확인한다. 이 문서는 법률 검토 전 자료이며, 출시 전 일본 변호사 자문을 받는다.

## 1. 도쿄 대중교통

ODPT 데이터 카탈로그(ckan.odpt.org) 378개 데이터셋을 라이선스별로 집계한 결과.

### 정밀 안내 (시간표 데이터 사용 가능)

| 사업자 | 라이선스 | 받는 데이터 |
| --- | --- | --- |
| 도쿄메트로 | ODPT 기본 라이선스 | 역, 노선, 역·열차 시간표, 운임, 운행 정보, 실시간 열차 |
| 도에이 지하철·도덴 아라카와선·닛포리-토네리 라이너·도에이 버스 | CC BY 4.0 | 시간표, 운행 정보, 버스 GTFS-JP·버스 위치. 버스 GTFS-JP는 토큰 없이 공개(2026-11-16 적재, 출처 표기 "東京都交通局・公共交通オープンデータ協議会") |
| 린카이선, 쓰쿠바 익스프레스, 다마 모노레일, 유리카모메 | ODPT 기본 라이선스 | 역, 노선, 시간표 등. 유리카모메는 GTFS가 없어 역 시간표·운임(JSON)을 받아 직접 GTFS로 바꾼다(`wug-data build-odpt-gtfs`). 바꾼 파일은 우리 서버 안에서만 쓰고 배포하지 않는다 |
| 버스: 간토버스·게이오버스·오다큐버스·세이부버스, 주오구(에도버스)·메구로구·오타구·시나가와구 커뮤니티 버스 | ODPT 기본 라이선스 | GTFS(2026-11-16 적재). 니시도쿄버스·섬 지역은 23구 밖이라 넣지 않았다 |
| 버스·수상버스: 다이토구(메구린)·분쿄구(B-구루)·지요다구(가자구루마)·기타구(K버스), TOKYO CRUISE | CC BY 4.0 | GTFS, 토큰 없이 공개(2026-11-16 적재) |

### 근사 안내 ("예상" 배지)

| 사업자 | 이유 |
| --- | --- |
| JR 동일본, 게이오, 도부, 게이큐, 소테쓰, 도큐, 세이부, 오다큐, 가나가와추오 버스, 고쿠사이코교 버스, 도부 버스 | ODPT "챌린지 한정 라이선스". 공모전 응모용만 허용, 2027-03-12 이용 허락 종료 후 삭제 의무. JR 동일본은 "경쟁 서비스 금지" 조건이 공모전 뒤에도 존속 |
| 게이세이, 도쿄 모노레일 | ODPT에 없음 |
| JR 동일본 시간표 사이트(timetables.jreast.co.jp) | 교통신문사 「JR時刻表」 기반 데이터, "전자 매체 가공 금지" 명시 |

- **챌린지 한정 라이선스 데이터는 개발용으로도 받지 않는다.** 받는 순간 라이선스에 동의한 것으로 보며(제2조), 우리 데이터의 독립성을 입증하기 어려워진다.
- 근사 노선은 노선 모양·역 위치(국토수치정보 N02, OSM)와 요약 사실(시간대별 배차 간격, 첫차·막차, 역 간 소요 시간)로 배차 간격 기반 GTFS(`frequencies.txt`)를 만든다. 요약 사실은 사람이 공식 공개 정보에서 확인하고 출처를 기록한다(3장).
- 직통 운행(도에이 아사쿠사선 ↔ 게이큐·게이세이, 메트로 후쿠토신선 ↔ 도큐·도부 등)은 메트로·도에이 구간만 정밀, 사철 구간은 근사로 이어 붙인다.
- 운임: 메트로·도에이는 ODPT 운임 데이터. JR은 공개된 운임 규칙(거리 구간별 운임표)을 구현해 계산. 사철은 공식 운임 안내의 요약 사실로 근사.
- 실시간 운행 정보: 메트로·도에이만. 나머지는 운영사 운행 정보 페이지로 링크.

### ODPT 기본 라이선스·개발자 가이드라인 의무사항

1. 앱에 표시: 데이터가 공공교통 오픈데이터센터에서 제공된 것, 정확성·완전성이 보증되지 않는 것, 앱 문의처(운영사로 직접 문의하지 않도록). 가이드라인 예시 문구를 쓴다.
2. 정적 데이터(시간표 등): 취득 일시를 사용자가 볼 수 있는 화면에 표시. ODPT가 갱신을 알리면 **1주일 안에** 반영.
3. 동적 데이터(운행 정보·열차 위치): 데이터 생성 시각(`dc:date`) 표시, `odpt:frequency` 간격으로 갱신, 유효 범위(`dct:valid`)를 넘은 데이터는 표시하지 않음.
4. 원본·복원 가능한 파생 데이터를 제3자가 재이용 가능한 형태로 공개·재배포하지 않는다(제8조). 오프라인 묶음은 앱 내부 형식으로만 저장하고 내보내기 기능을 두지 않는다. 허용 범위는 ODPT 사무국에 문의(미결).
5. 기계 번역한 데이터(역 이름 한국어 등)는 기계 번역임을 표시.
6. 로고·노선 색은 가공하지 않고 쓴다. 도큐버스·ANA·린카이선·유리카모메·도쿄메트로 이미지에는 특정 이용 조건이 있다.

CC BY 4.0(도에이)은 출처 표기만 하면 된다.

## 2. 지도·장소·기타

| 데이터 | 라이선스 | 상업 이용 | 우리 방침 |
| --- | --- | --- | --- |
| OpenStreetMap | ODbL 1.0 | 가능 | 지도 타일(Protomaps)과 도보·자동차 경로에만 사용. **장소 데이터 출처로 쓰지 않는다**(OSM 장소에 우리 영업시간·태그를 붙이면 파생 DB가 되어 공개 의무가 생길 수 있음, OSMF Horizontal Layers 가이드라인) |
| Protomaps 타일 / 스타일·폰트·스프라이트 | ODbL / CC0·OFL·MIT | 가능 | 지도에 "Protomaps © OpenStreetMap" 표시 |
| Overture Maps Places | CDLA-Permissive-2.0 (Foursquare 출처분 Apache 2.0) | 가능 | 장소 후보. OSM과 조인하지 않는다. 일본 품질 미확인, confidence로 거르고 편집팀 검수 |
| Foursquare OS Places | Apache 2.0 | 가능 | 장소 후보 |
| Wikidata | CC0 | 가능 | 사실값(좌표, 공식 사이트 URL, 이름)만 |
| Wikipedia 본문 | CC BY-SA 4.0 | 조건부 | 쓰지 않는다(팁 DB에 share-alike가 번질 수 있음) |
| Wikimedia Commons 사진 | 파일별 | 대부분 가능 | 파일별 작가·라이선스·링크를 저장해 표시 |
| 국토수치정보 N02 철도, S12 역별 승하차, P11 버스 정류장 **2022년판** | CC BY 4.0 | 가능 | 근사 노선 모양·역 위치. 가공 사실을 명시 |
| 국토수치정보 **P12 관광자원**, P11 **2010년판** | 비상업 전용 | **불가** | 쓰지 않는다 |
| 도쿄도 오픈데이터 (Wi-Fi, 장애인 화장실, 도에이 역 정보 등) | CC BY 4.0 (예외 3건) | 가능 | 장소 부가 정보 |
| GTFS 데이터 저장소 (gtfs-data.jp) | 피드별, 대부분 CC BY 4.0 | 가능 | 23구 안은 커뮤니티 버스 2개뿐 |
| 국민의 축일 CSV (내각부) | 공공데이터 이용규약 1.0 | 가능 | 출처 표기 |
| JNTO japan.travel | 무단 복제·저장 금지 | **불가** | 쓰지 않는다 |
| Google Places | 장소 ID 외 저장 금지 | 저장 불가 | 출처로 쓰지 않는다 |
| Google Routes API | — | — | 일본 대중교통 미지원(Japan partners 제외). 도보 벤치마크에만 |

소프트웨어(OTP LGPL, Valhalla MIT, MapLibre BSD-2/MIT, OR-Tools Apache 2.0, PostGIS GPL, Pretendard·Noto OFL)는 상업 이용 가능. 앱에 오픈소스 라이선스 화면을 둔다. PostGIS GPL과 OTP LGPL의 서버 사용 해석은 법률 검토 항목.

## 3. 직접 수집·웹 수집 규칙

1. 1차 출처만: 장소·운영사 공식 사이트, 정부·지자체 공지, 역 게시물. 다른 앱·정보 모음 사이트(Google 지도, 타베로그, 트립어드바이저, NAVITIME, 乗換案内 등)는 출처로 쓰지 않는다.
2. 사실만: 시각, 금액, 주소, 휴무일, 배차 간격. 문장·사진·평점·후기는 가져오지 않는다. 팁·설명은 사실을 바탕으로 새로 쓴다.
3. 요약 사실만: 운영사 시간표를 전 역·전 열차 단위로 옮겨 DB를 만들지 않는다(JR 시간표 자체 구축 여부는 미결, 변호사 자문 후 결정).
4. 수집 전에 사이트 이용약관과 robots.txt를 확인한다. 자동 접근을 막는 사이트는 우회하지 않고 사람이 확인한다. 로그인이 필요한 페이지는 쓰지 않는다.
5. 자동 수집은 사이트당 요청 간격을 두고 느리게 한다.
6. 항목마다 출처 URL, 확인 날짜, 방법(사람·배치), 담당을 기록한다(`data_sources`, `places.verified_at`).

## 4. 다른 나라 (확장 때 참고)

| 지역 | 대중교통 데이터 | 메모 |
| --- | --- | --- |
| 한국 | 공공데이터포털, 서울 열린데이터광장, TAGO | 공공데이터법상 영리 이용 가능. GTFS 변환 필요, 도보·자동차는 OSM 품질 확인 |
| 대만 | TDX (GTFS 포함), 정부 오픈데이터 라이선스 | 유료화 계획 보도 있음, 확인 필요 |
| 미국·EU·영국·호주·싱가포르·홍콩 | 대체로 공개 GTFS | 기관·나라별 약관 확인 |
| 중국 | 공개 거의 없음, 지도 규제 | 무료로는 어려움 |

## 근거

- ODPT: [데이터 카탈로그](https://ckan.odpt.org/dataset), [기본 라이선스](https://developer.odpt.org/terms/data_basic_license.html), [개발자 가이드라인](https://developer.odpt.org/terms/data_basic_use_guideline.html), [챌린지 한정 라이선스](https://developer.odpt.org/challenge_license)
- JR 동일본: [사이트 이용 안내](https://www.jreast.co.jp/site/rules.html), [시간표 사이트](https://timetables.jreast.co.jp/)
- 교통신문사: [시각 데이터 판매](https://www.kotsu.co.jp/service/jikoku_solution/data_sales/)
- Google: [Directions API 일본 대중교통 ZERO_RESULTS](https://discuss.google.dev/t/directions-api-transit-mode-returns-zero-results/378267)
- 국토수치정보: [이용규약](https://nlftp.mlit.go.jp/ksj/other/agreement.html), [비상업 규약](https://nlftp.mlit.go.jp/ksj/other/agreement_02.html)
- [GTFS 데이터 저장소 규약](http://docs.gtfs-data.jp/site/kiyaku.pdf), [도쿄도 오픈데이터 규약](https://portal.data.metro.tokyo.lg.jp/terms/), [공공데이터 이용규약 1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0), [japan.travel 이용약관](https://www.japan.travel/en/terms-of-use/)
- [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), [OSMF Horizontal Layers](https://osmfoundation.org/wiki/Community_Guidelines/Horizontal_Map_Layers_-_Guideline), [Overture 출처 표기](https://docs.overturemaps.org/attribution/), [Foursquare OS Places](https://docs.foursquare.com/data-products/docs/access-fsq-os-places), [Wikidata 라이선스](https://www.wikidata.org/wiki/Wikidata:Licensing)
