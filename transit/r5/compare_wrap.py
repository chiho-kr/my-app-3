"""R5가 전날 서비스의 자정 이후 정차(24:00:00 이상 표기)를 탐색하는지 확인.

wrap 없는 피드(extract_gtfs.py --no-wrap --out capital_gtfs_nowrap.zip)와 wrap 피드(capital_gtfs.zip, 기본)로 서울 무작위 150점 OD를 평일 23:30/00:30/01:30에 계산해 비교.
같다면 R5가 이미 반영, wrap 쪽이 더 많이 도달하면 R5가 놓치는 것 → wrap 피드를 써야 한다.
2026-10-20 결과: 00:30 도달 OD쌍 6,537 → 7,412 (공통쌍 47.3% 더 빠름), 01:30 5,778 → 6,690, 23:30 12,423 → 12,445.

  python -m transit.r5.compare_wrap
"""

import argparse
import datetime as dt

import geopandas as gpd
import numpy as np
from shapely.geometry import Point

from common.paths import GTFS_NOWRAP_ZIP, GTFS_ZIP, OSM_CLIP
from transit.r5.r5util import load_network, r5py


def main() -> None:
    argparse.ArgumentParser(description="[검증] 자정 넘김 사본 유무에 따른 R5 심야 도달 비교").parse_known_args()  # r5py용 --max-memory는 통과
    rng = np.random.default_rng(0)
    pts = gpd.GeoDataFrame({"id": range(150)}, crs=4326, geometry=[
        Point(x, y) for x, y in zip(rng.uniform(126.84, 127.13, 150), rng.uniform(37.47, 37.66, 150))])
    res = {}
    for feed in [GTFS_NOWRAP_ZIP, GTFS_ZIP]:
        net = load_network(OSM_CLIP, [feed])
        for hh, day in [(23, 28), (0, 29), (1, 29)]:
            m = r5py.TravelTimeMatrix(net, origins=pts, destinations=pts,
                                      departure=dt.datetime(2026, 10, day, hh, 30),
                                      departure_time_window=dt.timedelta(minutes=30),
                                      transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
                                      max_time=dt.timedelta(minutes=90))
            res[(feed.name, hh)] = m[m.from_id != m.to_id].set_index(["from_id", "to_id"]).travel_time
        del net
    for hh in [23, 0, 1]:
        a, b = res[("capital_gtfs_nowrap.zip", hh)], res[(GTFS_ZIP.name, hh)]
        both = a.notna() & b.notna()
        print(f"{hh:02d}:30  90분 내 도달 OD쌍: 원본 {int(a.notna().sum())} → wrap {int(b.notna().sum())} | "
              f"공통쌍 중앙값 {a[both].median():.0f}→{b[both].median():.0f}분, "
              f"wrap이 더 빠름 {(b[both] < a[both]).mean():.1%}, 더 느림 {(b[both] > a[both]).mean():.1%}")


if __name__ == "__main__":
    main()
