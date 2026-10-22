"""MOTIS 사전계산용 도보 테이블 (시각과 무관, 한 번만).

MOTIS one-to-all은 '정류장'까지의 소요시간만 준다 → 약국까지 가려면 두 가지 도보 시간이 더 필요하다.
  1) 정류장 → 약국 도보 (내린 뒤 걷기): 약국마다 반경 RADIUS_STOP 안 가까운 정류장 최대 128곳, arriveBy
  2) 격자 칸 → 약국 직접 도보 (대중교통 안 탐): 칸마다 가까운 약국 64곳 + 21시 이후 영업하는 약국 중 가까운 64곳
도보는 MOTIS one-to-many(mode=WALK, OSM 보행망) 결과(초).

산출물: data/transit/motis_walk.npz
  stop_ids[str], stop_j/stop_s/stop_w : (약국 index, 정류장 index, 초)
  cell_ids, cell_i/cell_j/cell_w      : (격자 id, 약국 index, 초)

  python -m transit.motis_walk        (MOTIS 서버가 127.0.0.1:8080에 떠 있어야 함)
"""

import argparse
import datetime as dt
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

from common import motis
from common.paths import GRID, MOTIS_FEED, MOTIS_WALK
from common.pharmacies import load_pharmacies
from common.timeline import T0

RADIUS_STOP = 1500      # m, 정류장 ↔ 약국 후보 반경
MAX_WALK_STOP = 1200    # s, 정류장 → 약국 최대 도보 20분
MAX_WALK_DIRECT = 3600  # s, 칸 → 약국 직접 도보 최대 60분
K_ANY, K_NIGHT = 64, 64
WORKERS = 16
_to5179 = Transformer.from_crs(4326, 5179, always_xy=True)


def main() -> None:
    argparse.ArgumentParser(description="MOTIS 도보표 (정류장→약국, 칸→약국)").parse_args()
    ph, is_open = load_pharmacies()
    plat, plon = ph.geometry.y.to_numpy(), ph.geometry.x.to_numpy()
    pxy = np.c_[_to5179.transform(plon, plat)]

    stops = pd.read_csv(zipfile.ZipFile(MOTIS_FEED).open("stops.txt"), dtype={"stop_id": str})
    sxy = np.c_[_to5179.transform(stops.stop_lon.to_numpy(), stops.stop_lat.to_numpy())]
    stree = cKDTree(sxy)

    # 1) 정류장 → 약국
    t = time.time()
    def per_pharmacy(j):
        idx = stree.query_ball_point(pxy[j], RADIUS_STOP)
        if not idx:
            return []
        idx = sorted(idx, key=lambda s: np.hypot(*(sxy[s] - pxy[j])))[:128]
        d = motis.walk_one_to_many((plat[j], plon[j]), [(stops.stop_lat[s], stops.stop_lon[s]) for s in idx], MAX_WALK_STOP, True)
        return [(j, s, w) for s, w in zip(idx, d) if w is not None]
    with ThreadPoolExecutor(WORKERS) as ex:
        pairs = [p for ps in ex.map(per_pharmacy, range(len(ph))) for p in ps]
    sp = np.array(pairs, dtype=np.int64) if pairs else np.zeros((0, 3), np.int64)
    print(f"정류장→약국 {len(sp)}쌍, 약국당 평균 {len(sp) / len(ph):.0f}, {time.time() - t:.0f}s", flush=True)

    # 2) 칸 → 약국 직접 도보
    t = time.time()
    grid = pd.read_parquet(GRID)
    grid = grid[grid.in_seoul].reset_index(drop=True)
    gxy = grid[["x", "y"]].to_numpy()
    night = np.nonzero(is_open[:, 3 * 60:].any(axis=1))[0]          # 21:00 이후 한 번이라도 영업
    _, nn_any = cKDTree(pxy).query(gxy, k=K_ANY)
    _, nn_night = cKDTree(pxy[night]).query(gxy, k=K_NIGHT)
    def per_cell(i):
        cand = list(dict.fromkeys([*nn_any[i].tolist(), *night[nn_night[i]].tolist()]))
        out = []
        for c in range(0, len(cand), 128):
            ch = cand[c:c + 128]
            d = motis.walk_one_to_many((grid.lat[i], grid.lon[i]), [(plat[j], plon[j]) for j in ch], MAX_WALK_DIRECT, False)
            out += [(int(grid.id[i]), j, w) for j, w in zip(ch, d) if w is not None]
        return out
    with ThreadPoolExecutor(WORKERS) as ex:
        cp = np.array([p for ps in ex.map(per_cell, range(len(grid))) for p in ps], dtype=np.int64)
    print(f"칸→약국 직접 도보 {len(cp)}쌍 (칸 {len(grid)}, 21시 이후 영업 약국 {len(night)}), {time.time() - t:.0f}s")

    np.savez_compressed(MOTIS_WALK, stop_ids=("ktdbgtfs_" + stops.stop_id).to_numpy(),
                        stop_j=sp[:, 0], stop_s=sp[:, 1], stop_w=sp[:, 2],
                        cell_i=cp[:, 0], cell_j=cp[:, 1], cell_w=cp[:, 2],
                        built=str(dt.datetime.now()), t0=str(T0))
    print("→", MOTIS_WALK)


if __name__ == "__main__":
    main()
