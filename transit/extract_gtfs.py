"""KTDB 전국 GTFS(2025-03) → 수도권 분석용 GTFS zip.

KTDB 명세서(202503_GTFS_설명서.pdf) 기준 처리:
  - route_type이 국제 표준과 다르다 → 표준 값으로 변환 (아래 ROUTE_TYPE)
      KTDB 0 시내/마을/농어촌버스 → 3 bus      KTDB 1 도시철도/경전철 → 1 subway
      KTDB 3 시외버스            → 3 bus      KTDB 4 일반철도      → 2 rail
      KTDB 5 공항리무진           → 3 bus      KTDB 6 고속철도      → 2 rail
      KTDB 2 해운, 7 항공         → 제외 (도시 내 접근성과 무관, 7은 표준에서 funicular라 오인 위험)
  - 평일 하루 시간표인데 calendar는 전 요일(20170101~20301231) 운행으로 코딩됨
      → 이 GTFS로 토·일을 계산하면 평일 배차가 적용된다. 평일 분석에만 쓴다.
  - 자정 이후는 24:00:00 이상 표기와 00:xx 표기가 함께 있다. 그대로 둔다.
    R5는 출발일 서비스만 탐색해서 전날 서비스의 25:30 같은 정차를 00:30 출발 질의에서 놓친다
    (compare_wrap.py, 2026-10-20: 00:30 출발 90분 내 도달 OD쌍 원본 6,537 → wrap 7,412).
    → 그 부분을 같은 날 00:xx 사본으로 추가한다 (wrap_overnight, 기본 켬, --no-wrap으로 끔).

영역: config.BBOX 안 정류장. 영역 밖 정류장은 stop_times에서 잘라내고(순서는 유지),
      영역 안 정차가 2개 미만인 trip은 버린다.

  python -m transit.extract_gtfs                                   # → capital_gtfs.zip (R5용, 자정 넘김 사본 포함)
  python -m transit.extract_gtfs --no-wrap --out capital_gtfs_nowrap.zip   # MOTIS용 → data/motis/ktdb.gtfs.zip 로 복사
"""

import argparse
import zipfile

import pandas as pd

from common.paths import BBOX, GTFS_ZIP, RAW_GTFS
from common.paths import TRANSIT as OUT

ROUTE_TYPE = {"0": "3", "1": "1", "3": "3", "4": "2", "5": "3", "6": "2"}  # 2(해운)·7(항공)은 제외


def _sec(s: pd.Series) -> pd.Series:
    p = s.str.split(":", expand=True).astype(int)
    return p[0] * 3600 + p[1] * 60 + p[2]


def _hms(x: pd.Series) -> pd.Series:
    return (x // 3600).map("{:02d}".format) + ":" + (x % 3600 // 60).map("{:02d}".format) + ":" + (x % 60).map("{:02d}".format)


def wrap_overnight(st: pd.DataFrame, trips: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """24:00:00 이상 시각을 가진 trip의 '익일 부분'을 같은 서비스일의 00:xx trip 사본으로 추가.

    R5는 출발일 서비스만 탐색하므로, 전날 서비스의 25:30 같은 정차는 00:30 출발 질의에서 잡히지 않는다.
    이 피드는 매일 같은 시간표(calendar 전 요일)라서 '전날 25:30' = '오늘 01:30'이 성립한다 → 사본이 정확히 같은 의미.
    사본은 trip_id + '_wrap', 시각 -24h, 00:00 이전 정차는 버림(2개 이상 남는 경우만).
    """
    a, d = _sec(st.arrival_time), _sec(st.departure_time)
    over = st.trip_id.isin(st.loc[d >= 86400, "trip_id"].unique())
    w = st[over & (a >= 86400)].copy()
    w["arrival_time"], w["departure_time"] = _hms(a[w.index] - 86400), _hms(d[w.index] - 86400)
    cnt = w.groupby("trip_id").size()
    w = w[w.trip_id.isin(cnt[cnt >= 2].index)]
    w["trip_id"] = w.trip_id + "_wrap"
    wt = trips[trips.trip_id.isin(set(w.trip_id.str[:-5]))].copy()
    wt["trip_id"] = wt.trip_id + "_wrap"
    print(f"자정 넘김 trip {over.groupby(st.trip_id).any().sum()}개 중 {wt.shape[0]}개의 익일 부분을 00:xx 사본으로 추가")
    return pd.concat([st, w], ignore_index=True), pd.concat([trips, wt], ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="KTDB 전국 GTFS → 수도권 분석용 GTFS zip")
    ap.add_argument("--no-wrap", action="store_true",
                    help="자정 넘김 사본을 넣지 않는다 (MOTIS용. 기본은 넣음: R5는 전날 서비스의 24시 이후 정차를 탐색하지 않는다)")
    ap.add_argument("--out", help="출력 파일 이름 (data/transit/ 안). r5py 캐시가 파일 이름 기준이라 비교용 피드는 이름을 달리한다")
    a = ap.parse_args()
    out_zip = OUT / a.out if a.out else GTFS_ZIP
    OUT.mkdir(parents=True, exist_ok=True)
    rd = lambda n, **kw: pd.read_csv(RAW_GTFS / f"{n}.txt", dtype=str, encoding="utf-8-sig", **kw)

    stops = rd("stops")
    lat, lon = stops.stop_lat.astype(float), stops.stop_lon.astype(float)
    stops = stops[lat.between(BBOX["lat_min"], BBOX["lat_max"]) & lon.between(BBOX["lon_min"], BBOX["lon_max"])]
    assert stops.stop_id.is_unique

    routes = rd("routes")
    n_routes = len(routes)
    routes = routes[routes.route_type.isin(ROUTE_TYPE)].copy()
    routes["route_type"] = routes.route_type.map(ROUTE_TYPE)
    trips = rd("trips")
    trips = trips[trips.route_id.isin(set(routes.route_id))]

    keep_stop, keep_trip = set(stops.stop_id), set(trips.trip_id)
    parts = []
    for ch in pd.read_csv(RAW_GTFS / "stop_times.txt", dtype=str, chunksize=3_000_000):
        parts.append(ch[ch.stop_id.isin(keep_stop) & ch.trip_id.isin(keep_trip)])
    st = pd.concat(parts, ignore_index=True)
    st["_seq"] = st.stop_sequence.astype(int)
    st = st.sort_values(["trip_id", "_seq"])
    cnt = st.groupby("trip_id").size()
    st = st[st.trip_id.isin(cnt[cnt >= 2].index)].drop(columns="_seq")

    if not a.no_wrap:
        st, trips = wrap_overnight(st, trips)
    trips = trips[trips.trip_id.isin(set(st.trip_id))]
    routes = routes[routes.route_id.isin(set(trips.route_id))]
    stops = stops[stops.stop_id.isin(set(st.stop_id))]
    transfers = rd("transfers")
    transfers = transfers[transfers.from_stop_id.isin(set(stops.stop_id)) & transfers.to_stop_id.isin(set(stops.stop_id))]

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in [("agency", rd("agency")), ("calendar", rd("calendar")), ("routes", routes), ("trips", trips),
                         ("stops", stops), ("stop_times", st), ("transfers", transfers)]:
            z.writestr(f"{name}.txt", df.to_csv(index=False))

    h = st.departure_time.str[:2].astype(int)
    print(f"routes {len(routes)}/{n_routes}  trips {len(trips)}  stops {len(stops)}  stop_times {len(st)}  transfers {len(transfers)}")
    print("route_type(표준):", routes.route_type.value_counts().to_dict())
    print("심야(N) 노선:", routes.route_short_name.str.match(r"^N\d", na=False).sum(),
          "| 출발시각 24시 이상 행:", int((h >= 24).sum()), "| 00~03시 행:", int(h.between(0, 3).sum()))
    print("→", out_zip, f"{out_zip.stat().st_size / 2**20:.0f} MB")


if __name__ == "__main__":
    main()
