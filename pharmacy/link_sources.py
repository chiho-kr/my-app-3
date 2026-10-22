"""[교차검증] 서울시 약국 데이터(약국ID, E-Gen 기관ID) ↔ pharm114(pharmacyCode) 레코드 매칭.

  python -m pharmacy.link_sources

두 source에 공통 PK가 없다 (서울시=E-Gen ID, pharm114=자체코드/요양기관번호/면허번호, 교집합 0).
그래서 좌표 근접(blocking) 후보쌍을 이름·전화·도로명주소로 점수화하고 1:1로 배정한다.

점수: 이름 일치 3 (유사 ≥0.8이면 2) + 전화 일치 3 + 도로명+건물번호 일치 3 + 거리(<30m 2, <100m 1)
채택: 점수 ≥ 5, 거리 < 300m (전화+이름/주소 일치면 거리 무관 — 한쪽 지오코딩 오류),
      주소만 일치하는 쌍은 이름 유사도 ≥ 0.6일 때만. 점수 내림차순·거리 오름차순으로 greedy 1:1.
tier:  A = 이름·전화·주소 중 2개 이상 일치,  B = 1개 일치 + 근접

산출물: data/crosscheck/link.csv (seoul_id, p114_ref, score, tier, dist_m, name_eq, phone_eq, addr_eq, 양쪽 이름/주소)
"""

import argparse
import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
from pyproj import Transformer

from common.paths import LINK_CSV, PHARM114, RAW_SEOUL_CSV

MAX_DIST = 300.0

_to5179 = Transformer.from_crs(4326, 5179, always_xy=True)
_ROAD = re.compile(r"(\S+구)\s+(\S+(?:로|길))\s+(?:지하\s*)?(\d+(?:-\d+)?)")  # "통일로16길 3" → 통일로16길|3


def norm_name(s: str) -> str:
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s or "")
    s = re.sub(r"[\s·.\-_]", "", s)
    return re.sub(r"약국$", "", s).lower()


def norm_phone(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d if len(d) >= 9 else ""


def road_key(s: str) -> str:
    m = _ROAD.search(s or "")
    return "|".join(m.groups()) if m else ""


def prep(df, id_col, name, addr, phone, lat, lon):
    df = df.fillna({name: "", addr: "", phone: ""})
    out = pd.DataFrame({
        "id": df[id_col], "name": df[name], "addr": df[addr],
        "n": df[name].map(norm_name), "ph": df[phone].map(norm_phone), "rk": df[addr].map(road_key),
    })
    x, y = _to5179.transform(df[lon].astype(float).values, df[lat].astype(float).values)
    out["x"], out["y"] = x, y
    return out.reset_index(drop=True)


def candidates(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    bx, by = b["x"].values, b["y"].values
    rows = []
    for i, (ax, ay) in enumerate(zip(a["x"].values, a["y"].values)):
        d = np.hypot(bx - ax, by - ay)
        for j in np.nonzero(d < MAX_DIST)[0]:
            rows.append((i, j, d[j]))
    # 같은 전화번호는 좌표가 어긋나도(지오코딩 오류) 후보로 포함
    ph = b[b.ph != ""].reset_index().groupby("ph")["index"].apply(list).to_dict()
    for i, p in enumerate(a["ph"]):
        for j in ph.get(p, []) if p else []:
            rows.append((i, j, float(np.hypot(bx[j] - a.x[i], by[j] - a.y[i]))))
    return pd.DataFrame(rows, columns=["i", "j", "dist"]).drop_duplicates(["i", "j"])


def score(a, b, c: pd.DataFrame) -> pd.DataFrame:
    an, bn = a.n.values[c.i], b.n.values[c.j]
    c["name_eq"] = an == bn
    sim = np.array([SequenceMatcher(None, x, y).ratio() if x and y else 0 for x, y in zip(an, bn)])
    c["name_sim"] = sim.round(2)
    c["phone_eq"] = (a.ph.values[c.i] == b.ph.values[c.j]) & (a.ph.values[c.i] != "")
    c["addr_eq"] = (a.rk.values[c.i] == b.rk.values[c.j]) & (a.rk.values[c.i] != "")
    c["score"] = (np.where(c.name_eq, 3, np.where(sim >= 0.8, 2, 0)) + 3 * c.phone_eq + 3 * c.addr_eq
                  + np.where(c.dist < 30, 2, np.where(c.dist < 100, 1, 0)))
    return c


def assign(c: pd.DataFrame) -> pd.DataFrame:
    c = c[(c.score >= 5) & ((c.dist < MAX_DIST) | c.phone_eq & (c.name_eq | c.addr_eq))]
    # 주소만 일치하면 같은 건물의 다른 약국일 수 있다 → 이름이 어느 정도 비슷할 때만 인정
    c = c[c.name_eq | c.phone_eq | (c.name_sim >= 0.6)]
    c = c.sort_values(["score", "dist"], ascending=[False, True])
    used_i, used_j, keep = set(), set(), []
    for r in c.itertuples():
        if r.i in used_i or r.j in used_j:
            continue
        used_i.add(r.i)
        used_j.add(r.j)
        keep.append(r.Index)
    m = c.loc[keep].copy()
    n_eq = m.name_eq.astype(int) + m.phone_eq.astype(int) + m.addr_eq.astype(int)
    m["tier"] = np.where(n_eq >= 2, "A", "B")
    return m


def main() -> None:
    argparse.ArgumentParser(description="서울시 CSV ↔ pharm114 레코드 매칭 → data/crosscheck/link.csv").parse_args()
    s = pd.read_csv(RAW_SEOUL_CSV, encoding="cp949", dtype=str)
    p = pd.read_csv(PHARM114 / "pharmacies.csv", dtype=str).dropna(subset=["lat", "lon"])
    a = prep(s, "약국ID", "약국명", "주소", "대표전화1", "병원위도", "병원경도")
    b = prep(p, "ref", "name", "addr_full", "phone", "lat", "lon")

    m = assign(score(a, b, candidates(a, b)))
    out = pd.DataFrame({
        "seoul_id": a.id.values[m.i], "p114_ref": b.id.values[m.j], "tier": m.tier.values, "score": m.score.values,
        "dist_m": m.dist.round(1).values, "name_eq": m.name_eq.values, "name_sim": m.name_sim.values,
        "phone_eq": m.phone_eq.values, "addr_eq": m.addr_eq.values,
        "seoul_name": a.name.values[m.i], "p114_name": b.name.values[m.j],
        "seoul_addr": a.addr.values[m.i], "p114_addr": b.addr.values[m.j],
    }).sort_values("seoul_id")
    LINK_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(LINK_CSV, index=False, encoding="utf-8-sig")

    print(f"서울시 {len(a)} / pharm114 {len(b)} → 매칭 {len(out)} ({len(out) / len(a):.1%} of 서울시)")
    print("tier:", out.tier.value_counts().to_dict())
    print("일치 조합 (name, phone, addr):", out.groupby(["name_eq", "phone_eq", "addr_eq"]).size().to_dict())
    print("거리 분위수(m):", out.dist_m.quantile([.5, .9, .99]).round(1).to_dict())


if __name__ == "__main__":
    main()
