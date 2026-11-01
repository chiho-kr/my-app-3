"""보고서 페이지(web/report.html)용 요약 데이터.

입력: data/dong/dong_indicators.parquet (dong.indicators), 약국 영업 행렬, web/data/meta.json (transit.pack_web)
출력: web/data/report.json  — 시간대별 서울 추이, 구별 표, 순위 상관, 민감도, 사례 동
      web/data/gu.geojson    — 자치구 경계 (행정동을 합친 것)

  python -m dong.report_data
"""

import argparse
import json

import geopandas as gpd
import numpy as np
import pandas as pd

from common.paths import DONG, SEOUL_BOUNDARY, WEB_DATA
from common.pharmacies import load_pharmacies
from common.timeline import N_STEPS, STEP_MIN

IND = ["D_2200", "LQ_2200", "G_0000", "R_2200", "gap_h", "old_share"]
CASE_COLS = ["adm_cd", "gu", "dong", "pop", "old_share", "N_all", "N_2200", "N_0000", "D_2200", "LQ_2200",
             "G0", "G_2200", "G_0000", "R_2200", "T_2200", "T_0000", "gap_h"]


def rank_pct(s: pd.Series, ascending: bool = False) -> pd.Series:
    return s.rank(ascending=ascending, method="min")


def pick_cases(d: pd.DataFrame) -> list[dict]:
    """서로 다른 상황을 보여 주는 동 6곳 (규칙으로 고름)."""
    rules = [
        ("밤 공급이 몰린 곳", lambda x: x.N_2200.idxmax()),
        ("공백이 없는 주거지", lambda x: x[x.gap_h == x.gap_h.min()]["pop"].idxmax()),
        ("동 안에 밤 약국이 없어도 이웃 덕에 짧은 곳", lambda x: x[(x.N_2200 == 0) & (x.N_all > 0)].gap_h.idxmin()),
        ("밤 약국이 있어도 공백이 긴 곳", lambda x: x[x.N_2200 > 0].gap_h.idxmax()),
        ("공백이 가장 긴 곳", lambda x: x.gap_h.idxmax()),
        ("고령 인구가 많고 공백이 긴 곳", lambda x: x[x.gap_h >= 8].old_share.idxmax()),
    ]
    out, seen = [], set()
    for why, rule in rules:
        i = rule(d.drop(index=list(seen)))   # 앞에서 고른 동은 빼고
        seen.add(i)
        row = d.loc[i, CASE_COLS].to_dict()
        row["why"] = why
        row["rank_gap"] = int(rank_pct(d.gap_h, ascending=True)[i])   # 1 = 공백 가장 짧음
        row["rank_D"] = int(rank_pct(d.D_2200)[i])
        out.append(row)
    return out


def main() -> None:
    argparse.ArgumentParser(description="보고서 페이지용 요약 데이터").parse_args()
    d = gpd.read_parquet(DONG.parent / "dong_indicators.parquet")
    meta = json.loads((WEB_DATA / "meta.json").read_text(encoding="utf-8"))

    # 시간대별 서울 추이 (10분 간격): 영업 약국 수(서울 경계 안), 30·15분 안 면적 비율
    ph, opn = load_pharmacies()
    seoul = gpd.read_file(SEOUL_BOUNDARY).geometry.iloc[0]
    in_seoul = ph.within(seoul).to_numpy()
    bands = meta["bands"]
    timeline = [{"m": k * STEP_MIN, "open": int(opn[in_seoul, k * STEP_MIN].sum()),
                 "p15": round(meta["stats"][k][bands.index(15)], 4), "p30": round(meta["stats"][k][bands.index(30)], 4)}
                for k in range(N_STEPS)]

    # 구별 (인구 가중 = 동 인구로 가중한 평균)
    w = lambda x, c: float((x[c] * x["pop"]).sum() / x["pop"].sum())
    gu = []
    for name, x in d.groupby("gu"):
        n22, n00 = int(x.N_2200.sum()), int(x.N_0000.sum())
        gu.append({"gu": name, "pop": int(x["pop"].sum()), "old_share": float(x.pop65.sum() / x["pop"].sum()),
                   "N_all": int(x.N_all.sum()), "N_2200": n22, "N_0000": n00,
                   "D_2200": n22 / x["pop"].sum() * 1e4,
                   "LQ_2200": (n22 / x.N_all.sum()) / (d.N_2200.sum() / d.N_all.sum()),
                   "G_0000": w(x, "G_0000"), "R_2200": w(x, "R_2200"), "gap_h": w(x, "gap_h"),
                   "gap_med": float(x.gap_h.median())})

    corr = d[IND].corr(method="spearman").round(3)
    sens = {
        "G_0000": {f"반감 {h}m": round(float(d.G_0000.corr(d[f"G_0000_h{h}"], method="spearman")), 3) for h in (500, 1200)},
        "gap_h": {f"기준 {m}분": round(float(d.gap_h.corr(d[f"gap_h{m}"], method="spearman")), 3) for m in (15, 45)},
    }
    # 공백 상위 10% 동이 기준을 바꿔도 상위 10%에 남는 비율
    top = lambda s: set(s.nlargest(int(len(s) * 0.1)).index)
    sens["gap_top10_keep"] = {f"기준 {m}분": round(len(top(d.gap_h) & top(d[f"gap_h{m}"])) / len(top(d.gap_h)), 3)
                              for m in (15, 45)}

    gw = lambda c: float((d[c] * d["pop"]).sum() / d["pop"].sum())
    summary = {
        "n_dong": len(d), "pop": int(d["pop"].sum()), "pop65": int(d.pop65.sum()),
        "n_ph": int(d.N_all.sum()), "open": {t: int(d[f"N_{t}"].sum()) for t in ("2000", "2200", "0000", "0200")},
        "dong_zero_2200": int((d.N_2200 == 0).sum()), "dong_zero_0000": int((d.N_0000 == 0).sum()),
        "gap_popw": gw("gap_h"), "gap_median": float(d.gap_h.median()),
        "pop_gap8": int(d.loc[d.gap_h >= 8, "pop"].sum()), "dong_gap8": int((d.gap_h >= 8).sum()),
        "R_2200_popw": gw("R_2200"), "R_0000_popw": gw("R_0000"),
        "old_hi_gap": float(d[d.old_share >= d.old_share.quantile(0.75)].gap_h.mean()),
        "old_lo_gap": float(d[d.old_share <= d.old_share.quantile(0.25)].gap_h.mean()),
    }

    out = {"summary": summary, "timeline": timeline, "gu": gu, "corr": {"cols": IND, "m": corr.values.tolist()},
           "sens": sens, "cases": pick_cases(d)}
    (WEB_DATA / "report.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":"),
                                                     default=lambda o: round(float(o), 4)), encoding="utf-8")
    g = d.dissolve("gu")[["geometry"]].reset_index().to_crs(4326)
    g["geometry"] = g.geometry.simplify(0.0002)
    g.to_file(WEB_DATA / "gu.geojson", driver="GeoJSON")
    print(json.dumps({"summary": summary, "sens": sens}, ensure_ascii=False, indent=1))
    print("cases:", [(c["gu"], c["dong"], c["why"]) for c in out["cases"]])
    print(corr.to_string())


if __name__ == "__main__":
    main()
