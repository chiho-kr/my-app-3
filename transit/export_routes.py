"""웹 지도용 노선도: 서울을 지나는 KTDB GTFS 노선 궤적 + 18:00~06:00 10분 단위 운행 대수.

- KTDB GTFS에는 shapes.txt가 없다 → MOTIS가 가져오기 때 OSM 도로·선로에 맞춘 궤적(/api/v6/trip legGeometry)을 쓴다.
- MOTIS config의 timetable.route_shapes(mode: missing)로 OSM 궤적을 계산해 둬야 한다 (없으면 정류장 사이 직선).
- 서울 경계+2km로 자르고, 궤적 계산이 안 된 긴 직선 노선은 뺀다.
- 노선마다 정차 수가 가장 많은 운행 하나를 대표로 궤적을 받는다 (KTDB 노선 id는 방향별로 나뉘어 있음).
- 운행 대수 n[k]: 18:00+10k분 시점에 첫 정류장을 출발했고 마지막 정류장에 아직 도착하지 않은 운행 수.
  피드는 매일 같은 평일 시간표라서, 자정 이후 구간은 '전날' 24시 이후 표기와 '당일' 00시대 표기를 모두 센다.
- 입력: data/motis/ktdb.gtfs.zip (MOTIS가 읽은 것과 같은 피드), 서울 경계, MOTIS 서버(127.0.0.1:8080)
- 출력: web/data/routes.geojson  properties: name, kind(rail 전철|express 광역·공항버스|city 시내버스|night 심야 N버스), color(공식 색), n(문자열, 73칸 운행 대수를 쉼표로)

  python -m transit.export_routes
"""

import argparse
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString

from common import motis
from common.paths import MOTIS_FEED, RAW_GTFS, SEOUL_BOUNDARY, WEB_DATA
from common.timeline import DAY, N_STEPS, STEP_MIN, T0

T0_MIN = T0.hour * 60 + T0.minute
SIMPLIFY_M = 20
CLIP_BUF = 2000          # m, 서울 경계 밖은 이만큼만 남긴다
MAX_STRAIGHT = 2000      # m, 궤적 계산이 안 돼 이보다 긴 직선이 남은 노선(시외·고속버스 등)은 뺀다


# 노선 공식 색 (웹에서 '교통망 강조' 시 채도를 낮춰 쓴다)
RAIL_COLOR = {
    "서울1호선": "#0052A4", "서울2호선": "#00A84D", "서울3호선": "#EF7C1C", "서울4호선": "#00A5DE",
    "서울5호선": "#996CAC", "서울6호선": "#CD7C2F", "서울7호선": "#747F00", "서울8호선": "#E6186C",
    "서울9호선": "#BDB092", "경의중앙선": "#77C4A3", "공항철도": "#0090D2", "경춘선": "#0C8E72",
    "수인분당선": "#FABE00", "신분당선": "#D4003B", "우이신설경전철": "#B0CE18", "서해선": "#8FC31F",
    "김포도시철도": "#A17800", "신림선": "#6789CA", "GTX-A": "#9A6292",
}
# 서울시 버스 색 체계: 간선(파랑)·지선(초록)·순환(노랑)·마을(연초록).
# 서울 밖(경기·인천) 시내버스는 공식 계열인 초록이되, 서울 노선과 경쟁하지 않게 탁한 초록 (웹에서 더 흐리게 그린다).
SEOUL_BUS_COLOR = {"trunk": "#3D5BAB", "branch": "#5BB025", "circular": "#F2B70A", "village": "#7DBE4E"}
OTHER_BUS_COLOR = "#5F8A6C"


def route_color(route_id: str, name: str, kind: str) -> str | None:
    """공식 색. 광역·공항(아주 연하게)과 심야 N(흰색)은 웹에서 고정 색을 쓰므로 None."""
    if kind == "rail":
        return RAIL_COLOR.get(name)
    if kind != "city":
        return None
    if not route_id.startswith("BR_TAGO_SEB"):
        return OTHER_BUS_COLOR
    n = name.strip()
    if re.search(r"[가-힣]", n):
        return SEOUL_BUS_COLOR["village"]
    if re.fullmatch(r"\d{3}", n):
        return SEOUL_BUS_COLOR["trunk"]
    if re.fullmatch(r"\d{4}", n):
        return SEOUL_BUS_COLOR["branch"]
    if re.fullmatch(r"\d{2}[A-Z]?", n):
        return SEOUL_BUS_COLOR["circular"]
    return SEOUL_BUS_COLOR["branch"]


def route_kind(route_id: str, name: str, ktdb_type: str) -> str | None:
    """KTDB 원래 route_type(명세서) + 노선 번호로 지도 구분. None이면 지도에서 뺀다.

    전철   : KTDB 1 도시철도/경전철 (광역전철 포함)
    광역   : KTDB 3 시외(직행좌석 포함)·5 공항, 그리고 KTDB 0(시내/마을)로 분류된 광역급 —
             M버스(M####), 프리미엄(P…), 경기 G버스(G####, 빨간 광역), 서울 9xxx, 경기·인천 등 서울 밖 4자리 번호
    심야   : 시내버스 중 N으로 시작
    시내   : 나머지 KTDB 0
    제외   : KTDB 4 일반철도·6 고속철도 (장거리 열차), 2 해운·7 항공
    """
    if ktdb_type == "1":
        return "rail"
    if ktdb_type in ("3", "5"):
        return "express"
    if ktdb_type != "0":
        return None
    n = name.strip()
    seoul = route_id.startswith("BR_TAGO_SEB")
    if re.match(r"^(M\d{4}|P\d|G\d{4})", n) or (seoul and re.match(r"^9\d{3}$", n)) or (not seoul and re.match(r"^\d{4}(-\d+)?$", n)):
        return "express"
    if n.startswith("N"):
        return "night"
    return "city"


def trip_geometry(trip_id: str, start_hhmm: str):
    """MOTIS 운행 id = 날짜_첫출발시각_데이터셋_원래trip_id."""
    return motis.trip_lines(f"{DAY:%Y%m%d}_{start_hhmm}_ktdbgtfs_{trip_id}")


def main() -> None:
    argparse.ArgumentParser(description="웹 지도용 노선도 (MOTIS 궤적 + 10분 단위 운행 대수)").parse_args()
    z = zipfile.ZipFile(MOTIS_FEED)
    rd = lambda n, **kw: pd.read_csv(z.open(f"{n}.txt"), dtype=str, encoding="utf-8-sig", **kw)
    routes, trips, stops = rd("routes"), rd("trips"), rd("stops")
    st = rd("stop_times", usecols=["trip_id", "departure_time", "arrival_time", "stop_id", "stop_sequence"])

    seoul = gpd.read_file(SEOUL_BOUNDARY).geometry.iloc[0]
    sp = gpd.GeoSeries(gpd.points_from_xy(stops.stop_lon.astype(float), stops.stop_lat.astype(float)), crs=4326)
    seoul_stops = set(stops.stop_id[sp.within(seoul).to_numpy()])

    st = st.merge(trips[["trip_id", "route_id"]], on="trip_id")
    sec = lambda s: s.str.split(":", expand=True).astype(int).pipe(lambda d: d[0] * 60 + d[1])
    st["dep"], st["arr"] = sec(st.departure_time), sec(st.arrival_time)
    st["seq"] = st.stop_sequence.astype(int)
    seoul_routes = set(st.route_id[st.stop_id.isin(seoul_stops)])
    st = st[st.route_id.isin(seoul_routes)]
    g = st.sort_values(["trip_id", "seq"]).groupby("trip_id")
    tr = pd.DataFrame({"route_id": g.route_id.first(), "start": g.dep.first(), "end": g.arr.last(),
                       "n_stops": g.size(), "start_hhmm": g.departure_time.first().str[:5]})

    # 10분 단위 운행 대수 (전날 24시 이후 + 당일 00시대 모두)
    bins = T0_MIN + STEP_MIN * np.arange(N_STEPS)
    counts = {}
    for rid, d in tr.groupby("route_id"):
        s, e = d.start.to_numpy(), d.end.to_numpy()
        n = [int(((s <= b) & (e > b)).sum() + ((s + 1440 <= b) & (e + 1440 > b)).sum()) for b in bins]
        counts[rid] = n

    # 대표 운행: 정차 수 최대, 같으면 24시 전에 출발하는 것 중 이른 것
    rep = (tr[tr.start < 1440].reset_index().sort_values(["route_id", "n_stops", "start"], ascending=[True, False, True])
           .drop_duplicates("route_id"))
    t = time.time()
    with ThreadPoolExecutor(16) as ex:
        geoms = list(ex.map(lambda r: trip_geometry(r.trip_id, r.start_hhmm), rep.itertuples()))
    print(f"서울 경유 노선 {len(rep)}개 궤적 {time.time() - t:.0f}s, 궤적 없음 {sum(not g for g in geoms)}개")

    rmeta = routes.set_index("route_id")
    ktdb_type = pd.read_csv(RAW_GTFS / "routes.txt", dtype=str, encoding="utf-8-sig").set_index("route_id").route_type
    clip = gpd.GeoSeries([seoul], crs=4326).to_crs(5179).buffer(CLIP_BUF).iloc[0]
    feats, n_straight, n_drop = [], 0, 0
    for r, lines in zip(rep.itertuples(), geoms):
        if not lines:
            continue
        name = str(rmeta.route_short_name.get(r.route_id, ""))
        kind = route_kind(r.route_id, name, str(ktdb_type.get(r.route_id, "")))
        if kind is None:
            n_drop += 1
            continue
        geom = (LineString(lines[0]) if len(lines) == 1 else MultiLineString(lines))
        g5 = gpd.GeoSeries([geom], crs=4326).to_crs(5179).iloc[0]
        segs = [np.hypot(*np.diff(np.asarray(l.coords), axis=0).T).max()
                for l in (g5.geoms if g5.geom_type == "MultiLineString" else [g5]) if len(l.coords) > 1]
        if segs and max(segs) > MAX_STRAIGHT:
            n_straight += 1
            continue
        g5 = g5.intersection(clip)
        if g5.is_empty:
            continue
        geom = gpd.GeoSeries([g5], crs=5179).simplify(SIMPLIFY_M).to_crs(4326).iloc[0]
        feats.append({"type": "Feature", "geometry": json.loads(gpd.GeoSeries([geom]).to_json())["features"][0]["geometry"],
                      "properties": {"name": name, "kind": kind, "color": route_color(r.route_id, name, kind),
                                     "seoul": r.route_id.startswith("BR_TAGO_SEB") or kind == "rail",
                                     "n": ",".join(map(str, counts[r.route_id]))}})
    out = WEB_DATA / "routes.geojson"
    txt = json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":"))
    # 좌표 소수 5자리로
    txt = re.sub(r"(\d+\.\d{5})\d+", r"\1", txt)   # 소수 5자리 ≈ 1m
    out.write_text(txt, encoding="utf-8")
    kinds = pd.Series([f["properties"]["kind"] for f in feats]).value_counts().to_dict()
    print(f"일반·고속철도 등 제외 {n_drop}개")
    print(f"직선 구간 {MAX_STRAIGHT}m 초과로 뺀 노선 {n_straight}개")
    print(f"노선 {len(feats)}개 {kinds} → {out} {out.stat().st_size / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
