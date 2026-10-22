"""[검증용] R5(r5py)판 접근성 계산 — 평일 18:00~익일 02:00, 5분 간격.

기준 결과는 MOTIS판(transit.compute_access). 이 R5판은 시점당 약 11분이 걸려 18:00~18:15 네 시점만 계산했고,
MOTIS와의 비교(중앙값 차이 +1분, 90%가 ±5분 이내)에 쓴다.

정의
  T_i(t) = min_j { tt_ij(t) : 약국 j가 도착 시각 t + tt_ij(t)에 영업 중 },  tt ≤ 60분 (초과는 255)
  - tt_ij(t): r5py, 출발 t 정각(시간창 1분), TRANSIT+WALK, 평일(2026-10-28 수) — KTDB GTFS는 평일 시간표
  - 약국 영업: E-Gen 규칙(hours.status_at), 서울+인접 경기·인천 약국 모두 후보 (경계효과)
  - GTFS: capital_gtfs.zip (자정 넘김 사본 포함, extract_gtfs.py)

속도: 출발지는 서울 칸(공식 격자 SECT_CD 11xxx, 10,125칸)만. 경계 밖 버퍼 칸은 계산하지 않는다(255).
      약국 후보를 직선거리로 줄여 봤지만(2026-10-20) 결과 99.9% 동일·속도 차이 10% 미만이라 쓰지 않는다
      — 병목은 약국 수가 아니라 출발지별 경로 탐색이다.
병렬: r5py TravelTimeMatrix는 출발지를 순차 처리하고 1분 시간창에선 코어를 거의 못 쓴다.
      한 JVM 안 스레드 병렬은 빨라지지 않아서(벤치마크), 프로세스 여러 개로 시점을 나눈다:
      python -m transit.r5.compute_access_r5 --part 0 --nparts 3   (… --part 1, --part 2 를 동시에)
결과: data/transit/access_{cell}m/t_HHMM.npz  (minutes uint8[n_grid], nearest int32[n_grid] = 약국 index, -1 없음; 서울 밖 칸은 255/-1)
      이미 있는 시점은 건너뛴다 (이어받기).

  python -m transit.r5.compute_access_r5 --part 0 --nparts 3
"""

import argparse
import datetime as dt
import time

import geopandas as gpd
import numpy as np
import pandas as pd

from common.paths import ACCESS_R5, GRID, GTFS_ZIP, OSM_CLIP
from common.pharmacies import DEST_ID_OFFSET, load_pharmacies
from common.timeline import MAX_TT, T0, minutes_since_t0

STEPS = [T0 + dt.timedelta(minutes=5 * k) for k in range(97)]   # 18:00 … 02:00
CHUNK = 1000                                   # 출발지 묶음 (행렬 메모리 제한)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", type=int, default=250)
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--nparts", type=int, default=1)
    ap.add_argument("--limit-origins", type=int, help="테스트용 (결과는 test_HHMM.npz)")
    a, _ = ap.parse_known_args()

    from transit.r5.r5util import load_network, r5py  # JVM 시작은 여기서 (인자 파싱 후)

    outdir = ACCESS_R5
    outdir.mkdir(parents=True, exist_ok=True)
    grid = pd.read_parquet(GRID)
    n_grid = int(grid.id.max()) + 1
    grid = grid[grid.in_seoul]
    if a.limit_origins:
        grid = grid.sample(a.limit_origins, random_state=0)
    grid = grid.sort_values("id").reset_index(drop=True)
    origins = gpd.GeoDataFrame(grid[["id"]], geometry=gpd.points_from_xy(grid.lon, grid.lat), crs=4326)
    ph, is_open = load_pharmacies()
    print(f"격자 {len(origins)}, 약국 {len(ph)}, 시점 {len(STEPS)} 중 part {a.part}/{a.nparts}", flush=True)

    net = load_network(OSM_CLIP, [GTFS_ZIP])

    def route(orig_idx, dest_idx, t, m0, best, near):
        """orig_idx(origins 행) × dest_idx(약국 index) 행렬 → 도착 시각 영업 약국 중 최단값을 best/near에 반영."""
        m = r5py.TravelTimeMatrix(net, origins=origins.iloc[orig_idx], destinations=ph.iloc[dest_idx], departure=t,
                                  departure_time_window=dt.timedelta(minutes=1),
                                  transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
                                  max_time=dt.timedelta(minutes=MAX_TT))
        m = m.dropna(subset=["travel_time"])
        tt = m.travel_time.to_numpy(int)
        j = m.to_id.to_numpy(int) - DEST_ID_OFFSET
        ok = is_open[j, np.minimum(m0 + tt, is_open.shape[1] - 1)]   # 도착 시각에 영업 중
        m = pd.DataFrame({"o": m.from_id.to_numpy(int)[ok], "tt": tt[ok], "j": j[ok]})
        if len(m):
            m = m.sort_values("tt").drop_duplicates("o")
            best[m.o.to_numpy()] = m.tt.to_numpy()
            near[m.o.to_numpy()] = m.j.to_numpy()

    for k, t in enumerate(STEPS):
        if k % a.nparts != a.part:
            continue
        f = outdir / f"t_{t:%H%M}.npz"
        if f.exists() and not a.limit_origins:
            continue
        t_start = time.time()
        m0 = minutes_since_t0(t)
        cand = np.nonzero(is_open[:, m0:m0 + MAX_TT + 1].any(axis=1))[0]   # 도착 가능 시간대에 한 번이라도 여는 약국
        best = np.full(n_grid, 255, dtype=np.uint8)
        near = np.full(n_grid, -1, dtype=np.int32)
        for st in range(0, len(origins), CHUNK):
            route(np.arange(st, min(st + CHUNK, len(origins))), cand, t, m0, best, near)
        np.savez_compressed(f if not a.limit_origins else outdir / f"test_{t:%H%M}.npz", minutes=best, nearest=near)
        sel = best[grid.id.to_numpy()]
        print(f"[{t:%H:%M}] 후보 약국 {len(cand)}, {time.time() - t_start:.0f}s, "
              f"중앙값 {np.median(sel[sel < 255]) if (sel < 255).any() else float('nan'):.0f}분, 60분 초과 {np.mean(sel == 255):.1%}",
              flush=True)

if __name__ == "__main__":
    main()
