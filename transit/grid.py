"""분석 격자: 국토지리정보원 국가지점번호 250m 격자(raw/grid/빈격자(250m).shp) + 서울 경계(OSM, 화면 표시용).

- 공식 격자: EPSG:5179, 250m 배수 경계, 필드 SPO_NO_CD(국가지점번호), SECT_CD(시군구 코드, 서울 = 11xxx).
- 서울 판정은 SECT_CD 앞 두 자리 '11' (공식 행정 귀속). 서울 밖 칸도 서울 경계에서 BUF m 안이면 남긴다
  (웹 지도에서 등시간 띠가 경계에서 잘려 보이지 않게 하는 용도, in_seoul=False).
- 경로 계산 출발점은 칸 중심점. 웹 래스터용 row/col은 칸 좌하단 좌표를 250으로 나눈 값의 상대 번호.
- 서울 경계선(seoul_boundary.geojson)은 OSM 행정경계 relation에서 뽑는다 (표시용).

산출물: data/transit/grid_250m.parquet (id,row,col,x,y,lon,lat,in_seoul,spo_no_cd,sect_cd)

  python -m transit.grid
"""

import argparse

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
import shapely.wkb

from common.paths import GRID, OSM_CLIP, SEOUL_BOUNDARY
from common.paths import RAW_GRID as GRID_SHP

CELL = 250
BUF = 1000


def seoul_boundary() -> gpd.GeoDataFrame:
    f = SEOUL_BOUNDARY
    if f.exists():
        return gpd.read_file(f)
    wkbf = osmium.geom.WKBFactory()
    geoms = []
    fp = (osmium.FileProcessor(str(OSM_CLIP)).with_areas(osmium.filter.TagFilter(("boundary", "administrative")))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)))
    for a in fp:
        if a.tags.get("admin_level") == "4" and a.tags.get("name") == "서울특별시" and not a.from_way():
            geoms.append(shapely.wkb.loads(wkbf.create_multipolygon(a), hex=True))
    assert len(geoms) == 1, f"서울 경계 {len(geoms)}개"
    g = gpd.GeoDataFrame({"name": ["서울특별시"]}, geometry=geoms, crs=4326)
    g.to_file(f, driver="GeoJSON")
    return g


def main() -> None:
    argparse.ArgumentParser(description="국토지리정보원 250m 격자 → 서울 분석 격자").parse_args()
    seoul = seoul_boundary().to_crs(5179).geometry.iloc[0]
    x0, y0, x1, y1 = seoul.buffer(BUF + CELL).bounds
    # 전국 170만 칸 중 서울 주변 bbox만 읽는다. 속성은 UTF-8 (shp에 cpg가 없어 명시)
    g = gpd.read_file(GRID_SHP, bbox=(x0, y0, x1, y1), encoding="utf-8")
    assert g.crs.to_epsg() == 5179, g.crs
    b = g.geometry.bounds
    assert np.allclose(b.maxx - b.minx, CELL) and np.allclose(b.maxy - b.miny, CELL), "250m 칸이 아닌 도형이 있음"
    # 시군구 경계에 걸친 칸은 같은 SPO_NO_CD로 시군구마다 한 행씩 들어 있다 → 칸 하나로 합치고,
    # 하나라도 서울 시군구면 in_seoul, 시군구 코드는 '|'로 이어 보존
    g["SECT_CD"] = g.SECT_CD.astype(str)
    sect = g.groupby("SPO_NO_CD").SECT_CD.agg(lambda s: "|".join(sorted(set(s))))
    g = g.drop_duplicates("SPO_NO_CD").copy()
    g["SECT_CD"] = g.SPO_NO_CD.map(sect)
    g["in_seoul"] = g.SECT_CD.str.split("|").map(lambda v: any(x.startswith("11") for x in v))
    b = g.geometry.bounds
    c = g.geometry.centroid
    keep = g.in_seoul | c.within(seoul.buffer(BUF))
    g, b, c = g[keep].copy(), b[keep], c[keep]
    g["x"], g["y"] = c.x.to_numpy(), c.y.to_numpy()
    g["col"] = ((b.minx - b.minx.min()) // CELL).astype(int).to_numpy()
    g["row"] = ((b.miny - b.miny.min()) // CELL).astype(int).to_numpy()
    assert not g.duplicated(["row", "col"]).any(), "같은 위치에 칸이 두 개"
    ll = gpd.GeoSeries(c, crs=5179).to_crs(4326)
    g["lon"], g["lat"] = ll.x.round(6).to_numpy(), ll.y.round(6).to_numpy()
    g = g.sort_values(["row", "col"]).reset_index(drop=True)
    g.insert(0, "id", g.index)
    out = pd.DataFrame(g[["id", "row", "col", "x", "y", "lon", "lat", "in_seoul"]])
    out["spo_no_cd"], out["sect_cd"] = g.SPO_NO_CD.to_numpy(), g.SECT_CD.astype(str).to_numpy()
    f = GRID
    out.to_parquet(f, index=False)
    print(f"공식 격자 {len(out)}칸 (서울 SECT_CD 11xxx {int(out.in_seoul.sum())}칸 = {out.in_seoul.sum() * 0.0625:.1f} km²), "
          f"래스터 {out.col.max() + 1}×{out.row.max() + 1}, 예시 {out.spo_no_cd.iloc[0]} → {f}")
    print("서울 구별 칸 수(앞 5개):", out[out.in_seoul].sect_cd.value_counts().head().to_dict())


if __name__ == "__main__":
    main()
