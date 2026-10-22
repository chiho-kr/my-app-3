"""CARTO Workspace 업로드용 약국 파일 (E-Gen 기준).

시각별 영업 여부를 열로 펼쳐서, Builder에서 위젯·필터로 시간대를 바꿔 볼 수 있게 한다.
  wed_18 … wed_01 : 평일(2026-10-28 수) 18시~익일 01시 정각 영업 여부 (1/0)
  sun_18 … sun_01 : 일요일(2026-11-01, 첫째 주) 같은 시각
  hol_18 … hol_01 : 수요일을 공휴일로 가정
  open_until_wed  : 평일 마지막 영업 시각 (예: 25.0 = 익일 01:00). 지도 색칠용

산출물: data/carto/pharmacies_carto.geojson (EPSG:4326, CARTO Data Warehouse로 import)

  python -m pharmacy.export_carto
"""

import argparse
import datetime as dt
import json

from common.hours import status_at
from common.paths import DATA, EGEN_JSONL
from pharmacy.egen_build import parse_egen

OUT = DATA / "carto"
HOURS = [18, 19, 20, 21, 22, 23, 0, 1]
WED, SUN = dt.date(2026, 10, 28), dt.date(2026, 11, 1)


def at(day: dt.date, h: int) -> dt.datetime:
    return dt.datetime.combine(day + dt.timedelta(days=1 if h < 6 else 0), dt.time(h))


def main() -> None:
    argparse.ArgumentParser(description="CARTO 업로드용 약국 GeoJSON (시각별 영업 여부 열)").parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    feats = []
    for line in EGEN_JSONL.open(encoding="utf-8"):
        r = json.loads(line)
        rules = parse_egen(r)
        p = {"ref": r["EMOGCODE"], "name": r.get("TITLE"), "addr": r.get("ADDRROAD"), "tel": r.get("TEL"),
             "sido": {"11": "서울", "21": "경기", "14": "인천"}.get(r.get("EMOGLOCA"), r.get("EMOGLOCA")),
             "in_seoul": int(r.get("EMOGLOCA") == "11")}
        addr = (r.get("ADDRROAD") or "").split()
        p["gugun"] = addr[1] if len(addr) > 1 else None
        for label, day, hol in [("wed", WED, frozenset()), ("sun", SUN, frozenset()), ("hol", WED, frozenset({WED}))]:
            for h in HOURS:
                p[f"{label}_{h:02d}"] = int(status_at(rules, at(day, h), holidays=hol) == "open")
        wed = next((x for x in rules if x.day == 2 and x.status == "open"), None)
        p["open_until_wed"] = round(wed.close_min / 60, 2) if wed else None
        feats.append({"type": "Feature", "properties": p,
                      "geometry": {"type": "Point", "coordinates": [float(r["LON"]), float(r["LAT"])]}})
    out = OUT / "pharmacies_carto.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    seoul = [f["properties"] for f in feats if f["properties"]["in_seoul"]]
    print(f"{len(feats)}곳 (서울 {len(seoul)}) → {out} {out.stat().st_size / 2**20:.1f} MB")
    print("서울 평일 영업:", {f"{h:02d}시": sum(p[f'wed_{h:02d}'] for p in seoul) for h in HOURS})


if __name__ == "__main__":
    main()
