"""서울 행정동 경계 + 주민등록 인구 (총인구, 65세 이상).

- 경계: vuski/admdongkor ver20261001 (adm_cd2 = 10자리 행정기관코드)
- 인구: 행정안전부 주민등록 인구통계 > 연령별 인구현황 > 월간, 전체읍면동현황, 5세 단위, 2026년 9월 말
  (jumin.mois.go.kr, downloadCsvAge.do, xlsStats=3, CP949). '행정구역' 칸 끝의 (10자리 코드)로 경계와 잇는다.
- 출력: data/dong/dong_seoul.parquet  adm_cd, gu, dong, pop, pop65, geometry(EPSG:5179), area_km2

  python -m dong.build_dong
"""

import argparse

import geopandas as gpd
import pandas as pd

from common.paths import DONG, RAW_DONG, RAW_POP

OLD_COLS = ["65~69세", "70~74세", "75~79세", "80~84세", "85~89세", "90~94세", "95~99세", "100세 이상"]


def load_population() -> pd.DataFrame:
    raw = pd.read_csv(RAW_POP, dtype=str, encoding="cp949")
    num = lambda c: raw[c].str.replace(",", "").astype(int)
    col = lambda suffix: next(c for c in raw.columns if c.endswith("_계_" + suffix))
    code = raw["행정구역"].str.extract(r"\((\d{10})\)\s*$")[0]
    pop = pd.DataFrame({"adm_cd": code, "pop": num(col("총인구수")),
                        "pop65": sum(num(col(c)) for c in OLD_COLS)})
    # 읍면동만 (시도·시군구 합계 행은 끝 5자리가 00000), 폐지된 동은 인구 0
    return pop[~pop.adm_cd.str.endswith("00000") & (pop["pop"] > 0)]


def main() -> None:
    argparse.ArgumentParser(description="서울 행정동 경계 + 주민등록 인구").parse_args()
    g = gpd.read_file(RAW_DONG)
    g = g[g.sido == "11"].drop(columns="adm_cd").rename(columns={"adm_cd2": "adm_cd", "sggnm": "gu"})
    g["dong"] = g.adm_nm.str.split().str[-1]
    pop = load_population()
    pop = pop[pop.adm_cd.str.startswith("11")]

    m = g.merge(pop, on="adm_cd", how="outer", indicator=True)
    no_pop, no_geom = m[m._merge == "left_only"], m[m._merge == "right_only"]
    print(f"경계 {len(g)}동, 인구 {len(pop)}동, 일치 {(m._merge == 'both').sum()}동")
    if len(no_pop):
        print("인구 없는 경계:", ", ".join(no_pop.adm_nm))
    if len(no_geom):
        print("경계 없는 인구 코드:", ", ".join(no_geom.adm_cd))

    d = gpd.GeoDataFrame(m[m._merge == "both"][["adm_cd", "gu", "dong", "pop", "pop65", "geometry"]],
                         geometry="geometry", crs=g.crs).to_crs(5179)
    d["area_km2"] = d.area / 1e6
    DONG.parent.mkdir(parents=True, exist_ok=True)
    d.reset_index(drop=True).to_parquet(DONG)
    print(f"서울 {len(d)}동 인구 {d['pop'].sum():,} (65세 이상 {d.pop65.sum():,}, "
          f"{d.pop65.sum() / d['pop'].sum():.1%}) → {DONG}")


if __name__ == "__main__":
    main()
