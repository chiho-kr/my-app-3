"""행정동 단위 심야 약국 지표 (수업 지표 3개 + 직접 설계 1개).

분석 단위: 서울 행정동 427개. 기준일 2026-10-28(수) 평일 밤, 시점 S = 20:00, 22:00, 00:00, 02:00.
O_j(t) = 약국 j가 시각 t에 영업 중이면 1 (E-Gen 영업시간 규칙, common.pharmacies).

1. 밀도 D_i(t)      = 동 안 영업 약국 수(t) / 주민등록 인구 × 10,000                   (인구 1만 명당)
2. 특화도 LQ_i(t)    = (N_i(t) / N_i) / (N(t) / N)                                     (N_i = 동 안 전체 약국 수)
                      1보다 크면 서울 평균보다 그 시각 영업 비중이 높은 동. N_i = 0인 동은 정의되지 않음.
3. 중력 접근성 G_i(t) = 동 안 250m 격자 평균 Σ_j O_j(t) · 0.5^(d_cj / 800m)           (d: 직선거리, 4km까지)
                      반감 거리 800m ≈ 도보 10분. 서울 밖 약국도 포함 (경계 효과 방지).
4. 심야 공백 시간 H_i  = 동 안 격자 평균 '30분 안에 문 연 약국에 닿지 못하는 시간'(시간)
   (직접 설계)         18:00~06:00 10분 간격 73시점의 대중교통+도보 최소 소요시간 T_c(t)
                      (transit.compute_access, MOTIS) 중 T_c(t) > 30분인 시점 수 × 10분 / 60.
   보조: T_i(t) = 동 안 격자 소요시간 중앙값(60분 초과는 61로 둠), P30_i(t) = 30분 안 격자 비율.
5. 유지율 R_i(t)     = G_i(t) / G⁰_i,  G⁰ = 영업시간을 무시하고 모든 약국을 넣은 중력 접근성
                      (입지 효과와 영업시간 효과를 나눠 보기 위함)
민감도: 반감 거리 500·1,200m (G_0000_h…), 공백 기준 15·45분 (gap_h15·45) → 순위 상관은 dong.report_data

격자→동: 격자 중심점이 들어 있는 동 (격자는 면적 가중과 같음, 동 안 인구 분포는 반영하지 않음).

출력: data/dong/dong_indicators.parquet(경계 포함), dong_indicators.csv, web/data/dong.geojson

  python -m dong.indicators
"""

import argparse

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from common.paths import ACCESS_MOTIS, DONG, GRID, WEB_DATA
from common.pharmacies import load_pharmacies
from common.timeline import MAX_TT, STEP_MIN, STEPS

TIMES = ["2000", "2200", "0000", "0200"]
T_MIN = {"2000": 120, "2200": 240, "0000": 360, "0200": 480}     # 18:00부터 분 (영업 행렬 열)
HALF_M = 800
MAX_M = 4000
GAP_MIN = 30


def gravity(cells_xy: np.ndarray, ph_xy: np.ndarray, open_now: np.ndarray, half: float = HALF_M) -> np.ndarray:
    tree = cKDTree(ph_xy[open_now])
    d = tree.sparse_distance_matrix(cKDTree(cells_xy), MAX_M, output_type="coo_matrix")
    w = np.power(0.5, d.data / half)
    return np.bincount(d.col, weights=w, minlength=len(cells_xy))


def main() -> None:
    argparse.ArgumentParser(description="행정동 단위 심야 약국 지표").parse_args()
    dong = gpd.read_parquet(DONG)

    ph, opn = load_pharmacies()
    ph = ph.to_crs(5179)
    ph_xy = np.c_[ph.geometry.x, ph.geometry.y]
    ph_dong = gpd.sjoin(ph, dong[["adm_cd", "geometry"]], predicate="within", how="left").adm_cd.to_numpy()
    in_seoul = pd.notna(ph_dong)

    grid = pd.read_parquet(GRID)
    grid = grid[grid.in_seoul].reset_index(drop=True)
    cells = gpd.GeoDataFrame(grid[["id"]], geometry=gpd.points_from_xy(grid.x, grid.y), crs=5179)
    cell_dong = gpd.sjoin(cells, dong[["adm_cd", "geometry"]], predicate="within", how="left").adm_cd
    cell_dong = cell_dong[~cell_dong.index.duplicated()].reindex(cells.index)
    print(f"서울 격자 {len(cells)}칸 중 동 안 {cell_dong.notna().sum()}칸, 서울 약국 {in_seoul.sum()}곳")
    cells_xy = np.c_[grid.x, grid.y]

    # 격자 × 73시점 소요시간
    tt = np.stack([np.load(ACCESS_MOTIS / f"t_{t:%H%M}.npz")["minutes"][grid.id.to_numpy()] for t in STEPS], axis=1)
    tt = np.where(tt > MAX_TT, MAX_TT + 1, tt).astype(float)

    c = pd.DataFrame({"adm_cd": cell_dong.to_numpy()})
    c["gap_h"] = (tt > GAP_MIN).sum(axis=1) * STEP_MIN / 60
    c["G0"] = gravity(cells_xy, ph_xy, np.ones(len(ph_xy), dtype=bool))
    for m in (15, 45):                                   # 민감도
        c[f"gap_h{m}"] = (tt > m).sum(axis=1) * STEP_MIN / 60
    for h in (500, 1200):
        c[f"G_0000_h{h}"] = gravity(cells_xy, ph_xy, opn[:, T_MIN["0000"]], h)
    step_of = {f"{t:%H%M}": k for k, t in enumerate(STEPS)}
    for s in TIMES:
        c[f"T_{s}"] = tt[:, step_of[s]]
        c[f"P30_{s}"] = (tt[:, step_of[s]] <= GAP_MIN).astype(float)
        c[f"G_{s}"] = gravity(cells_xy, ph_xy, opn[:, T_MIN[s]])
    agg = {col: ("median" if col.startswith("T_") else "mean") for col in c.columns if col != "adm_cd"}
    cell_ind = c.dropna(subset=["adm_cd"]).groupby("adm_cd").agg(agg)
    cell_ind["n_cells"] = c.groupby("adm_cd").size()

    # 약국 수 (동 안)
    phd = pd.DataFrame({"adm_cd": ph_dong[in_seoul]})
    for s in TIMES:
        phd[f"N_{s}"] = opn[in_seoul, T_MIN[s]].astype(int)
    cnt = phd.groupby("adm_cd").agg(N_all=("adm_cd", "size"), **{f"N_{s}": (f"N_{s}", "sum") for s in TIMES})

    d = dong.merge(cnt, on="adm_cd", how="left").merge(cell_ind, on="adm_cd", how="left")
    d[cnt.columns] = d[cnt.columns].fillna(0).astype(int)
    d["old_share"] = d.pop65 / d["pop"]
    N_all = d.N_all.sum()
    for s in TIMES:
        d[f"D_{s}"] = d[f"N_{s}"] / d["pop"] * 1e4
        d[f"LQ_{s}"] = (d[f"N_{s}"] / d.N_all.where(d.N_all > 0)) / (d[f"N_{s}"].sum() / N_all)
        d[f"R_{s}"] = d[f"G_{s}"] / d.G0

    out = DONG.parent
    d.to_parquet(out / "dong_indicators.parquet")
    d.drop(columns="geometry").to_csv(out / "dong_indicators.csv", index=False, encoding="utf-8-sig")
    web = d.to_crs(4326).copy()
    web["geometry"] = web.geometry.simplify(0.0001)
    keep = ["adm_cd", "gu", "dong", "pop", "pop65", "old_share", "N_all", "gap_h", "G0", "n_cells"] + \
           [f"{k}_{s}" for s in TIMES for k in ("N", "D", "LQ", "G", "R", "T", "P30")]
    web[keep + ["geometry"]].round(4).to_file(WEB_DATA / "dong.geojson", driver="GeoJSON")
    print(f"→ {out / 'dong_indicators.parquet'}, {WEB_DATA / 'dong.geojson'}")

    pd.set_option("display.width", 200)
    print(d[[f"{k}_{s}" for k in ("N", "D", "LQ", "G", "T", "P30") for s in ("2200", "0000")] + ["gap_h", "old_share"]]
          .describe().T.round(2))


if __name__ == "__main__":
    main()
