"""프로젝트 경로 한곳 모음. 모든 스크립트는 여기서 경로를 가져온다."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # seoul_pharmacy/

# ---- 원자료 (직접 받아 두는 것)
RAW = ROOT / "raw"
RAW_GTFS = RAW / "GTFS" / "202503_GTFS_DataSet"     # KTDB 전국 GTFS (2025-03)
RAW_OSM = RAW / "osm" / "south-korea-latest.osm.pbf"  # Geofabrik
RAW_GRID = RAW / "grid" / "빈격자(250m).shp"          # 국토지리정보원 국가지점번호 250m 격자
RAW_SEOUL_CSV = RAW / "서울시 약국 운영시간  정보.csv"  # 서울 열린데이터 OA-20402

# ---- 가공 데이터
DATA = ROOT / "data"

EGEN = DATA / "egen"                                # 기준 출처: E-Gen 약국
EGEN_JSONL = EGEN / "egen_pharmacies.jsonl"

PHARM114 = DATA / "pharm114"                        # 교차검증 출처: 휴일지킴이약국
PHARM114_RAW = PHARM114 / "raw"

CROSSCHECK = DATA / "crosscheck"                    # 출처 간 매칭·비교
LINK_CSV = CROSSCHECK / "link.csv"

TRANSIT = DATA / "transit"                          # GTFS·OSM·격자·접근성 계산
GTFS_ZIP = TRANSIT / "capital_gtfs.zip"             # 수도권 GTFS (R5용, 자정 넘김 사본 포함)
GTFS_NOWRAP_ZIP = TRANSIT / "capital_gtfs_nowrap.zip"
OSM_CLIP = TRANSIT / "capital.osm.pbf"
SEOUL_BOUNDARY = TRANSIT / "seoul_boundary.geojson"
GRID = TRANSIT / "grid_250m.parquet"
MOTIS_WALK = TRANSIT / "motis_walk.npz"
ACCESS_MOTIS = TRANSIT / "access_250m_motis"        # 기준 결과 (10분 간격)
ACCESS_R5 = TRANSIT / "access_250m"                 # R5 검증 결과 (5분 간격, 18:00~18:15만)

MOTIS_DIR = DATA / "motis"                          # MOTIS 작업 폴더 (config.yml, data/)
MOTIS_FEED = MOTIS_DIR / "ktdb.gtfs.zip"            # MOTIS가 읽는 GTFS (자정 넘김 사본 없음)
MOTIS_OSM = MOTIS_DIR / "capital_motis.osm.pbf"

# ---- 웹
WEB_DATA = ROOT / "web" / "data"

# ---- 분석 영역: 서울 + 약 15~20km 여유 (서울 출발 60~90분 이동을 담기 위함)
BBOX = dict(lat_min=37.20, lat_max=37.90, lon_min=126.55, lon_max=127.45)
