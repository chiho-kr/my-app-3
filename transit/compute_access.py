"""격자별 '가장 가까운 영업 약국까지' 대중교통+도보 소요시간 — MOTIS, 평일 18:00~익일 06:00, 10분 간격.

R5판(transit/r5/compute_access_r5.py, 5분 간격, 시점당 약 11분)을 대체한다. 시점당 0.5~5분.

정의 (R5판과 같음)
  T_i(t) = min_j { 소요(i→j, 출발 t) : 약국 j가 도착 시각에 영업 중 }, 60분 초과는 255
계산
  - 대중교통: MOTIS one-to-all(칸 → 도달 정류장 s, 분 d_s) + 정류장→약국 도보 w_sj (motis_walk.py)
              T = min_s ( d_s + min_j{ w_sj : j가 t+d_s+w_sj에 영업 } )
  - 직접 도보: 칸→약국 도보 (motis_walk.py)
  - 01:00 전에는 30분까지만 먼저 탐색하고 못 찾은 칸만 60분으로 다시 묻는다 (60분 단일 탐색과 결과 동일, 속도용).
    01:00 이후엔 30분 안에 찾는 칸이 드물어 바로 60분.
입력: MOTIS 서버(127.0.0.1:8080, data/motis), data/transit/motis_walk.npz
결과: data/transit/access_250m_motis/t_HHMM.npz (minutes uint8[n_grid], nearest int32[n_grid])
      이미 있는 시점은 건너뛴다. --budget 초가 지나면 현재 시점까지 끝내고 멈춘다 (나눠 실행용).

  python -m transit.compute_access --budget 540
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from common import motis
from common.paths import ACCESS_MOTIS as OUTDIR
from common.paths import GRID, MOTIS_WALK
from common.pharmacies import load_pharmacies
from common.timeline import MAX_TT, STEPS, minutes_since_t0, to_utc_iso

EARLY_CAP = 30            # 1차 탐색 상한(분)
EARLY_UNTIL = 420         # T0부터 420분(01:00) 전까지만 1차 탐색을 쓴다
WORKERS = 16
BIG = 10_000


def stop_table(w, is_open, m0, n_stops):
    """f[s, d] = 정류장 s에 d분에 도착했을 때 그때 영업 중인 약국까지 최소 도보(분), fj = 그 약국."""
    s, j, wm = w["stop_s"], w["stop_j"], np.ceil(w["stop_w"] / 60).astype(int)
    order = np.lexsort((wm, s))            # 정류장별로 묶고, 그 안에서 도보시간 오름차순
    s, j, wm = s[order], j[order], wm[order]
    us, start = np.unique(s, return_index=True)
    pos = np.arange(len(s))
    f = np.full((n_stops, MAX_TT + 1), BIG, np.int32)
    fj = np.full((n_stops, MAX_TT + 1), -1, np.int32)
    for d in range(MAX_TT + 1):
        ok = is_open[j, np.minimum(m0 + d + wm, is_open.shape[1] - 1)]
        first = np.minimum.reduceat(np.where(ok, pos, len(s)), start)   # 묶음 안 첫 영업 약국 = 최단 도보
        has = first < len(s)
        f[us[has], d] = wm[first[has]]
        fj[us[has], d] = j[first[has]]
    return f, fj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=1e9, help="이 초가 지나면 현재 시점을 끝내고 멈춤")
    ap.add_argument("--limit-origins", type=int, help="테스트용 (결과는 test_HHMM.npz)")
    ap.add_argument("--steps", help="테스트용 시점 HHMM 목록 (쉼표)")
    a = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    started = time.time()

    grid = pd.read_parquet(GRID)
    n_grid = int(grid.id.max()) + 1
    grid = grid[grid.in_seoul]
    if a.limit_origins:
        grid = grid.sample(a.limit_origins, random_state=0)
    grid = grid.sort_values("id").reset_index(drop=True)
    _, is_open = load_pharmacies()
    w = dict(np.load(MOTIS_WALK, allow_pickle=True))
    sid = {k: i for i, k in enumerate(w["stop_ids"])}
    n_stops = len(w["stop_ids"])
    ci, cj, cw = w["cell_i"], w["cell_j"], np.ceil(w["cell_w"] / 60).astype(int)

    steps = STEPS if not a.steps else [t for t in STEPS if f"{t:%H%M}" in a.steps.split(",")]
    for t in steps:
        f_out = OUTDIR / (f"test_{t:%H%M}.npz" if a.limit_origins else f"t_{t:%H%M}.npz")
        if f_out.exists() and not a.limit_origins:
            continue
        if time.time() - started > a.budget:
            print("시간 예산 소진 — 다시 실행하면 이어서 계산합니다", flush=True)
            break
        t1 = time.time()
        m0 = minutes_since_t0(t)
        t_utc = to_utc_iso(t)
        f, fj = stop_table(w, is_open, m0, n_stops)

        best = np.full(n_grid, 255, np.uint8)
        near = np.full(n_grid, -1, np.int32)
        # 직접 도보
        ok = (cw <= MAX_TT) & is_open[cj, np.minimum(m0 + cw, is_open.shape[1] - 1)]
        dm = pd.DataFrame({"i": ci[ok], "j": cj[ok], "w": cw[ok]}).sort_values("w").drop_duplicates("i")
        best[dm.i.to_numpy()] = dm.w.to_numpy()
        near[dm.i.to_numpy()] = dm.j.to_numpy()

        def per_cell(r, cap):
            res = motis.one_to_all(r.lat, r.lon, t_utc, cap)
            idx = np.array([sid.get(s, -1) for s, _ in res], int)
            d = np.array([x for _, x in res], int)
            keep = (idx >= 0) & (d <= MAX_TT)
            idx, d = idx[keep], d[keep]
            if not len(idx):
                return r.id, BIG, -1
            tot = d + f[idx, d]
            k = int(np.argmin(tot))
            return r.id, int(tot[k]), int(fj[idx[k], d[k]])

        cap = EARLY_CAP if m0 < EARLY_UNTIL else MAX_TT
        with ThreadPoolExecutor(WORKERS) as ex:
            res = list(ex.map(lambda r: per_cell(r, cap), grid.itertuples()))
        n_retry = 0
        if cap < MAX_TT:
            # 30분 안에 못 찾은 칸만 60분으로 다시
            got = {i: v for i, v, _ in res}
            retry = grid[[min(got[i], int(best[i])) > cap for i in grid.id]]
            n_retry = len(retry)
            with ThreadPoolExecutor(WORKERS) as ex:
                res += list(ex.map(lambda r: per_cell(r, MAX_TT), retry.itertuples()))
        for i, v, j in res:
            if v <= MAX_TT and v < best[i]:
                best[i], near[i] = v, j
        np.savez_compressed(f_out, minutes=best, nearest=near)
        sel = best[grid.id.to_numpy()]
        print(f"[{t:%H:%M}] {time.time() - t1:.0f}s (60분 재탐색 {n_retry}칸), 중앙값 "
              f"{np.median(sel[sel < 255]):.0f}분, 60분 초과 {np.mean(sel == 255):.1%}", flush=True)


if __name__ == "__main__":
    main()
