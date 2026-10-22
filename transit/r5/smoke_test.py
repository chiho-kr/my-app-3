"""r5py 동작 확인: 네트워크 빌드 + 몇 개 출발지에서 시각별 대중교통 이동시간.

평일(2026-10-28 수) 10:00 / 22:00 / 00:30 / 01:30 출발, 30분 시간창, 중앙값.
자정 이후 출발에서 전날 서비스(24:xx 표기 trip)가 잡히는지가 핵심 확인 사항.

  python -m transit.r5.smoke_test
"""

import argparse
import datetime as dt  # noqa: E402
import time  # noqa: E402

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from common.paths import GTFS_ZIP, OSM_CLIP  # noqa: E402
from transit.r5.r5util import load_network, r5py  # noqa: E402  (JAVA_HOME·JVM 메모리·캐시 갱신)

PTS = {  # 출발·도착 시험 지점
    "시청": (126.9779, 37.5663), "강남역": (127.0276, 37.4979), "노원역": (127.0616, 37.6551),
    "화곡역": (126.8496, 37.5416), "잠실역": (127.1001, 37.5133), "은평 진관동": (126.9220, 37.6390),
}


def main() -> None:
    argparse.ArgumentParser(description="[검증] r5py 네트워크 빌드와 시각별 시험 경로").parse_known_args()  # r5py용 --max-memory는 통과
    t = time.time()
    net = load_network(OSM_CLIP, [GTFS_ZIP])
    print(f"네트워크 빌드 {time.time() - t:.0f}s")
    pts = gpd.GeoDataFrame({"id": list(PTS)}, geometry=[Point(*v) for v in PTS.values()], crs=4326)
    day = dt.date(2026, 10, 28)
    for hh, mm in [(10, 0), (22, 0), (0, 30), (1, 30)]:
        when = dt.datetime.combine(day + dt.timedelta(days=1 if hh < 5 else 0), dt.time(hh, mm))
        for modes, label in [([r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK], "transit+walk"),
                             ([r5py.TransportMode.WALK], "walk")]:
            t = time.time()
            m = r5py.TravelTimeMatrix(net, origins=pts, destinations=pts, departure=when,
                                      departure_time_window=dt.timedelta(minutes=30), transport_modes=modes,
                                      max_time=dt.timedelta(minutes=120))
            m = m[m.from_id != m.to_id]
            pv = m.pivot(index="from_id", columns="to_id", values="travel_time").loc[list(PTS), list(PTS)]
            print(f"\n[{when:%m-%d %H:%M}] {label}  ({time.time() - t:.1f}s, 결측 {int(pv.isna().sum().sum())})")
            if label == "transit+walk":
                print(pv.to_string())


if __name__ == "__main__":
    main()
