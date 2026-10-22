# 서울 심야 약국 접근성

평일 저녁 6시부터 다음 날 아침 6시까지, 서울 250m 격자마다 **지금 문 연 가장 가까운 약국까지 대중교통+도보로 몇 분**인지 10분 간격으로 계산하고, 1분 단위 슬라이더 지도(`web/`)로 보여준다.

- 약국 영업시간: E-Gen(국립중앙의료원 응급의료포털) 약국 찾기, 2026-10 수집
- 대중교통: KTDB 수도권 GTFS (2025-03 평일 1일 시간표)
- 도로·보행망: OpenStreetMap (Geofabrik 한국)
- 격자: 국토지리정보원 국가지점번호 250m 격자
- 경로 계산: MOTIS 2.11 (검증: R5/r5py)

## 폴더 구조

```
common/         공용 모듈
  paths.py        모든 입출력 경로
  timeline.py     기준일(2026-10-28 수)·시간창(18:00~06:00, 10분)
  hours.py        영업시간 규칙(Rule)과 특정 시각 영업 판정(status_at)
  pharmacies.py   E-Gen 약국 목록 + 분 단위 영업 행렬
  motis.py        MOTIS HTTP 클라이언트
pharmacy/       약국 영업시간 수집·정리
  egen_collect.py, egen_build.py          기준 출처 (E-Gen)
  pharm114_collect.py, pharm114_build.py, pharm114_hours.py   교차검증 출처 (휴일지킴이약국)
  seoul_csv_build.py, link_sources.py     교차검증 (서울시 CSV, 출처 간 매칭)
  export_carto.py                         CARTO 업로드용 GeoJSON (선택)
transit/        대중교통·격자·접근성 계산
  extract_gtfs.py, clip_osm.py, osm_for_motis.py, grid.py    입력 준비
  motis_walk.py, compute_access.py                           접근성 계산 (MOTIS)
  export_routes.py, pack_web.py                              웹 데이터
  r5/             R5 검증 (compute_access_r5, compare_wrap, smoke_test, r5util)
motis/config.yml  MOTIS 설정 사본 (실제 작업 폴더는 data/motis/)
tests/          영업시간 파서 테스트
web/            지도 (index.html, app.js, data/)
raw/            원자료 (직접 받음, 저장소 제외)
data/           가공 데이터 (저장소 제외)
```

모든 스크립트는 프로젝트 루트에서 `python -m 패키지.모듈`로 실행한다. `--help`로 옵션을 볼 수 있다.

## 준비

```
pip install -r requirements.txt
```

- Python 3.14에서 확인했다.
- **MOTIS 2.11.3** (Windows): [GitHub 릴리스](https://github.com/motis-project/motis/releases)의 `motis-windows.zip`을 풀어 둔다 (여기서는 `%LOCALAPPDATA%\Programs\motis-2.11.3`).
- R5 검증(`transit/r5/`)을 돌릴 때만 Java 21이 필요하다. `r5util.py`가 `%LOCALAPPDATA%\Programs\jdk-21*`을 찾아 `JAVA_HOME`을 지정한다.

원자료 (`raw/`):

| 경로 | 출처 |
|---|---|
| `raw/GTFS/202503_GTFS_DataSet/` | KTDB GTFS (2025-03) |
| `raw/osm/south-korea-latest.osm.pbf` | Geofabrik |
| `raw/grid/빈격자(250m).shp` | 국토지리정보원 250m 격자 |
| `raw/서울시 약국 운영시간  정보.csv` | 서울 열린데이터광장 OA-20402 (교차검증용) |

## 실행 순서

```
# 1. 약국 영업시간 (E-Gen)
python -m pharmacy.egen_collect                          # → data/egen/egen_pharmacies.jsonl (요청 30회)
python -m pharmacy.egen_build --ref-date 2026-10-26      # → data/egen/ 테이블·품질 리포트

# 2. 입력 준비
python -m transit.extract_gtfs                           # → data/transit/capital_gtfs.zip (R5용)
python -m transit.extract_gtfs --no-wrap --out capital_gtfs_nowrap.zip
copy data\transit\capital_gtfs_nowrap.zip data\motis\ktdb.gtfs.zip
python -m transit.clip_osm                               # → data/transit/capital.osm.pbf
python -m transit.osm_for_motis                          # → data/motis/capital_motis.osm.pbf
python -m transit.grid                                   # → data/transit/grid_250m.parquet, seoul_boundary.geojson

# 3. MOTIS (data/motis 폴더에서, motis/config.yml을 그곳에 둔다)
motis.exe import                                         # 약 5분 (노선 궤적 계산 포함)
motis.exe server                                         # http://127.0.0.1:8080 — 아래 4·5단계 동안 켜 둔다

# 4. 접근성 계산
python -m transit.motis_walk                             # 도보표, 약 1분
python -m transit.compute_access --budget 530            # 73개 시점, 나눠 실행하면 이어서 계산 (전체 약 1시간)

# 5. 웹 데이터
python -m transit.export_routes                          # → web/data/routes.geojson
python -m transit.pack_web                               # → web/data/ (frames, meta, pharmacies …)

# 확인
python -m tests.test_hours
python -m http.server 8765 --directory web               # http://localhost:8765
```

교차검증 (선택):

```
python -m pharmacy.pharm114_collect                      # → data/pharm114/raw/ (약 30분)
python -m pharmacy.pharm114_build --ref-date 2026-10-26  # → data/pharm114/
python -m pharmacy.link_sources                          # → data/crosscheck/link.csv
python -m pharmacy.seoul_csv_build --ref-date 2026-10-26 # → data/crosscheck/hybrid/
python -m pharmacy.egen_build --ref-date 2026-10-26      # crosscheck_p114.csv 포함해 다시 생성
python -m transit.r5.smoke_test                          # R5 동작 확인
python -m transit.r5.compare_wrap                        # 자정 넘김 처리 검증
python -m transit.r5.compute_access_r5 --part 0 --nparts 2   # R5판 접근성 (느림)
```

## 지표 정의

`T_i(t) = min_j { 소요(i→j, 출발 t) : 약국 j가 도착 시각에 영업 중 }`, 60분 초과는 255.

- 출발지: 서울 격자 칸 중심 (10,125칸)
- 도착지: 서울과 인접 경기·인천 약국 9,279곳 (경계효과 방지)
- 대중교통: MOTIS one-to-all(칸→도달 정류장) + 정류장→약국 도보
- 걷기만: 칸→약국 도보
- 지도 띠: 생활SOC 기준 3·5·10·15·30분. 30분 초과는 회색

## 데이터 판단 근거

**약국 영업시간 출처**
- **E-Gen을 기준으로 쓴다.** 시간이 없는 요일은 운영하지 않는 것으로 본다. 서울 전체에서 일요일 시간이 없는 약국은 일요일 주차 플래그가 `NNNNN`인 약국과, 공휴일 시간이 없는 약국은 `HOLIDAYYN=N`인 약국과 예외 없이 일치한다.
- **pharm114**:
  - 평일 종료 `29:30`(익일 05:30)이 약 150곳에 몰려 있는데, 입력 오류로 판단했다. 서울시 CSV와 매칭해 보면 같은 약국이 19~21시에 닫는다.
  - E-Gen과의 일치율(pharm114 unknown 제외)은 수 10시 97.7%, 수 22시 96.2%, 일 12시 85.0%, 일 20시 95.6%다.
- **서울시 CSV**: E-Gen과 ID 체계가 같고(`약국ID` = `EMOGCODE`), 평일·토 값은 99.5% 이상 같다. 하지만 일요일·공휴일 열은 거의 모든 약국에 값이 채워져 있어 쓰지 않는다.
- **출처 간 매칭** (`link_sources`): 서울시 CSV와 pharm114 사이에는 공통 키가 없다. 그래서 이름·전화·도로명주소·거리로 점수를 매겨 1:1로 짝지었고, 서울시 5,619곳 중 4,892곳(87.1%)이 매칭됐다.

**GTFS·OSM 처리**
- **KTDB GTFS**:
  - 평일 하루 시간표인데 calendar는 모든 요일 운행으로 코딩되어 있다. 그래서 **평일 분석에만 쓴다**.
  - `route_type`을 국제 표준으로 바꿨다 (명세서 기준).
  - `shapes.txt`가 없어서, 노선 궤적은 MOTIS `route_shapes`가 OSM에 맞춰 만든 것을 쓴다.
- **자정 넘김**: R5는 전날 시간표의 24:00 이후 정차를 탐색하지 않는다. 그래서 R5용 피드에는 그 부분을 같은 날 00시대 사본으로 넣었다(`compare_wrap`: 00:30 출발 90분 내 도달 쌍 6,537 → 7,412). MOTIS는 스스로 처리하므로 사본이 없는 피드를 쓴다.
- **MOTIS 도로망 제한**: 노드 하나에 길(way)이 16개까지만 붙을 수 있다. 그래서 서울 OSM에서 이를 넘는 노드 76곳을 쪼갰다(`osm_for_motis`).
- **r5py 캐시 주의**: r5py는 입력 파일을 이름 기준으로 캐시하고, 내용이 바뀌어도 다시 복사하지 않는다. 그래서 `transit/r5/r5util.load_network()`가 바뀐 캐시 사본을 교체한다.

**계산 엔진**
- **MOTIS vs R5**: 18:00·18:10 서울 칸 기준으로 중앙값 차이는 +1분(MOTIS가 분 단위로 올림)이고, 90%가 ±5분 이내다. 시점 하나에 R5는 약 11분, MOTIS는 0.5~5분이 걸린다.

## 결과 요약 (서울 면적 중 30분 안에 영업 약국에 닿는 비율)

| 18:00 | 22:00 | 01:00 | 02:00~04:00 | 06:00 |
|---|---|---|---|---|
| 92% | 82% | 11% | 6% | 43% |

06:00에 비율이 회복되는 건 첫차 덕분이다. 영업 약국은 06:00에도 서울에 4곳뿐이다.

## 한계

- 영업시간은 약국이 신고한 값이다.
- 평일 시간표 하나만으로 계산했다. 주말 배차는 반영하지 않았다.
- 3분 띠는 250m 격자(걸어서 약 3분) 해상도에 가까워 거칠다.
- 도착 시각에 영업 중이어야 하므로, 지도에 켜져 있어도 곧 닫는 약국은 결과에서 빠질 수 있다 (팝업이 "도착 전에 마감"으로 안내).
- 보행 경로는 OSM 연결 상태를 따른다. 칸 중심이 하천 위에 있거나, 분리도로의 횡단 연결이 OSM에 없으면 크게 돌아간다 (예: 양천구 남부순환로 앞 칸, 직선 397m → 경로 1,277m). 가까운 영업 약국(400m 안)이 있는데 15분 넘게 나오는 칸은 시각마다 2~75칸(전체의 1% 미만).
- 실제 수요(밤에 약이 필요한 사람)와 대체 수단(편의점 안전상비의약품, 응급실)은 다루지 않았다.

## 참고: awesome-transit 검토 (2026-10-20)

- **쓴 것**: R5/r5py(검증), MOTIS(기준 계산·노선 궤적)
- **후보로 남긴 것**: MobilityData/gtfs-validator(공식 GTFS 검증 보고서), gtfs_kit(정류장별 운행 빈도 지표)
- **안 쓴 것**: pfaedle은 MOTIS `route_shapes`로 대체했다. OSM 기반 GTFS 생성 도구는 KTDB가 있어 필요 없었다.
- Mobility Database에는 한국 피드가 없다.

## 데이터 출처와 라이선스

`web/data/`에는 아래 자료에서 만든 결과가 들어 있다.

- 약국 위치·영업시간: 국립중앙의료원 응급의료포털(E-Gen) 약국 찾기 (2026-10 수집)
- 대중교통 시간표: 한국교통연구원 국가교통DB(KTDB) GTFS (2025-03)
- 노선 궤적·보행망·서울 경계: © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/)
- 분석 격자: 국토지리정보원 국가지점번호 250m 격자
- 배경지도: © CARTO, © OpenStreetMap contributors

