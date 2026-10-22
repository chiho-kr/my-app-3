"""E-Gen 약국 목록과 분 단위 영업 여부 행렬. 접근성 계산·웹 데이터가 같은 약국 순서(index)를 공유한다."""

import datetime as dt
import json

import geopandas as gpd
import numpy as np

from common.hours import status_at
from common.paths import EGEN_JSONL
from common.timeline import OPEN_MATRIX_MIN, T0
from pharmacy.egen_build import parse_egen

# r5py는 from_id == to_id인 쌍의 이동시간을 0으로 덮어쓴다 → 약국 id를 격자 id와 겹치지 않게 띄운다
DEST_ID_OFFSET = 1_000_000


def load_rows() -> list[dict]:
    with open(EGEN_JSONL, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def open_matrix(rules_list: list) -> np.ndarray:
    """약국 × 분(T0부터 OPEN_MATRIX_MIN분) 영업 여부. 같은 규칙 묶음은 한 번만 계산."""
    mins = [T0 + dt.timedelta(minutes=m) for m in range(OPEN_MATRIX_MIN)]
    cache, out = {}, np.zeros((len(rules_list), len(mins)), dtype=bool)
    for k, rules in enumerate(rules_list):
        key = tuple((r.day, r.week_of_month, r.status, r.open_min, r.close_min) for r in rules)
        if key not in cache:
            cache[key] = np.array([status_at(rules, m) == "open" for m in mins])
        out[k] = cache[key]
    return out


def load_pharmacies() -> tuple[gpd.GeoDataFrame, np.ndarray]:
    """(약국 GeoDataFrame[id=DEST_ID_OFFSET+index, ref], 영업 행렬[약국, 분])."""
    rows = load_rows()
    gdf = gpd.GeoDataFrame({"id": DEST_ID_OFFSET + np.arange(len(rows)), "ref": [r["EMOGCODE"] for r in rows]},
                           geometry=gpd.points_from_xy([float(r["LON"]) for r in rows], [float(r["LAT"]) for r in rows]),
                           crs=4326)
    return gdf, open_matrix([parse_egen(r) for r in rows])
