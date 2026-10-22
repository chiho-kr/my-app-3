"""[교차검증 출처] 서울 열린데이터광장 「서울시 약국 운영시간 정보」(OA-20402) → 분석 테이블 + 품질 리포트.

결론: 평일·토는 E-Gen과 같지만 일요일·공휴일 열은 신뢰하기 어렵다 (거의 모든 약국에 값이 채워져 있음).
기준 출처는 E-Gen(pharmacy.egen_build)이고, 이 스크립트는 그 판단의 근거를 남기는 용도다.

입력: raw/서울시 약국 운영시간  정보.csv  (cp949, 1행=약국, 요일별 진료시간 S(시작)/C(종료) "HHMM")
  - 종료가 2400을 넘으면 익일 (예: 2600 = 02:00). C < S 이면 자정을 넘긴 것으로 본다.
  - 값이 비어 있는 요일은 unknown (휴무로 추정하지 않음). 이 source에는 '휴무' 표기가 없다.
  - 좌표: 병원경도 = lon, 병원위도 = lat.

산출물: data/crosscheck/hybrid/ (기본: 평일·토 서울시 + 일요일 pharm114) 또는 data/crosscheck/seoul_csv/ (--sunday seoul)
  pharmacies.csv / .geojson, hours_rules.csv, open_by_hour.csv

  python -m pharmacy.seoul_csv_build --ref-date 2026-10-26
"""

import argparse
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from common.hours import PH, Rule, as_opening_hours, interval, status_at
from common.paths import CROSSCHECK, LINK_CSV, PHARM114, RAW_SEOUL_CSV

SRC = RAW_SEOUL_CSV
DAY_COLS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일", "공휴일"]  # index = Rule.day (7=PH)


def hhmm(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    v = str(v).strip().zfill(4)
    return f"{v[:-2]}:{v[-2:]}"


def parse_row(row) -> list[Rule]:
    rules = []
    for day, col in enumerate(DAY_COLS):
        s, c = hhmm(row[f"진료시간({col})S"]), hhmm(row[f"진료시간({col})C"])
        day = PH if day == 7 else day
        iv = interval(s, c)
        if iv is None:
            rules.append(Rule(row["약국ID"], day, 0, "unknown", source="seoul"))
        else:
            rules.append(Rule(row["약국ID"], day, 0, "open", iv[0], iv[1], source="seoul"))
    return rules


def next_weekday(start: dt.date, wd: int) -> dt.date:
    return start + dt.timedelta(days=(wd - start.weekday()) % 7)


def load_p114_sunday() -> tuple[dict[str, dict], dict[str, list[Rule]]]:
    """link.csv(서울시 ID → pharm114 ref)와 pharm114 일요일 규칙(n번째 주 포함)."""
    link = pd.read_csv(LINK_CSV, dtype=str).set_index("seoul_id")[["p114_ref", "tier"]].to_dict("index")
    sun: dict[str, list[Rule]] = {}
    for r in pd.read_csv(PHARM114 / "hours_rules.csv", dtype=str).query("day == '6'").itertuples():
        sun.setdefault(r.ref, []).append(Rule(
            r.ref, 6, int(r.week_of_month), r.status,
            int(r.open_min) if pd.notna(r.open_min) else None, int(r.close_min) if pd.notna(r.close_min) else None,
            r.inferred == "True", "p114:" + r.source))
    return link, sun


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--sunday", choices=["seoul", "p114"], default="p114",
                    help="p114: 평일·토는 서울시, 일요일은 매칭된 pharm114 규칙(미매칭은 unknown), 공휴일은 unknown")
    ap.add_argument("--out", type=Path, help="기본: data/hybrid (p114) / data/seoul (seoul)")
    ap.add_argument("--ref-date", type=dt.date.fromisoformat, default=dt.date.today(),
                    help="open_by_hour 기준 주 (이 날짜 이후 첫 수/토/일). 공휴일이 끼지 않은 주를 고를 것")
    a = ap.parse_args()
    a.out = a.out or CROSSCHECK / ("hybrid" if a.sunday == "p114" else "seoul_csv")
    a.out.mkdir(parents=True, exist_ok=True)
    link, p114_sun = load_p114_sunday() if a.sunday == "p114" else ({}, {})

    df = pd.read_csv(a.src, encoding="cp949", dtype=str)
    feats, all_rules = [], []
    for _, row in df.iterrows():
        rules = parse_row(row)
        lk = link.get(row["약국ID"])
        if a.sunday == "p114":
            # 서울시 일요일·공휴일 열은 신뢰 불가 (README 참조) → 일요일은 pharm114, 공휴일은 unknown
            rules = [r for r in rules if r.day < 6]
            sun = [Rule(row["약국ID"], 6, x.week_of_month, x.status, x.open_min, x.close_min, x.inferred, x.source)
                   for x in p114_sun.get(lk["p114_ref"], [])] if lk else []
            rules += sun or [Rule(row["약국ID"], 6, 0, "unknown", source="unmatched")]
            rules.append(Rule(row["약국ID"], PH, 0, "unknown", source="dropped"))
        all_rules += rules
        feats.append({
            "ref": row["약국ID"],
            "name": row["약국명"],
            "addr_full": row["주소"],
            "phone": row["대표전화1"],
            "postcode": f"{row['우편번호1'] or ''}{row['우편번호2'] or ''}",
            "lat": float(row["병원위도"]) if pd.notna(row["병원위도"]) else None,
            "lon": float(row["병원경도"]) if pd.notna(row["병원경도"]) else None,
            "gugun": (row["주소"] or "").split()[1] if len((row["주소"] or "").split()) > 1 else None,
            "opening_hours": as_opening_hours(rules),
            "updated": row["작업일시"],
            "p114_ref": lk["p114_ref"] if lk else None,
            "link_tier": lk["tier"] if lk else None,
            "_rules": rules,
        })

    cols = [k for k in feats[0] if not k.startswith("_")]
    with open(a.out / "pharmacies.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(feats)
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]} if f["lat"] else None,
         "properties": {k: f[k] for k in cols}} for f in feats]}
    (a.out / "pharmacies.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    with open(a.out / "hours_rules.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["ref", "day", "week_of_month", "status", "open_min", "close_min", "inferred", "source"])
        w.writerows([[r.ref, r.day, r.week_of_month, r.status, r.open_min, r.close_min, r.inferred, r.source] for r in all_rules])

    ref_days = {"wed": next_weekday(a.ref_date, 2), "sat": next_weekday(a.ref_date, 5), "sun": next_weekday(a.ref_date, 6)}
    rows = []
    for label, day in ref_days.items():
        for h in list(range(18, 24)) + list(range(0, 7)):
            when = dt.datetime.combine(day + dt.timedelta(days=1 if h < 7 else 0), dt.time(h))
            c = Counter(status_at(f["_rules"], when) for f in feats)
            rows.append({"day": label, "date": day.isoformat(), "at": when.isoformat(timespec="minutes"),
                         "open": c["open"], "closed": c["closed"], "unknown": c["unknown"]})
    with open(a.out / "open_by_hour.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # ---- 품질 리포트
    print(f"약국 {len(feats)}건, ID 중복 {len(feats) - df['약국ID'].nunique()}, 좌표 없음 {sum(f['lat'] is None for f in feats)}")
    print("구별:", dict(sorted(Counter(f["gugun"] for f in feats).items())))
    dn = ["월", "화", "수", "목", "금", "토", "일", "공휴일"]
    for d in list(range(7)) + [PH]:
        c = Counter(r.status for r in all_rules if r.day == d)
        print(f"  {dn[d]}: open {c['open']} unknown {c['unknown']}")
    raw_bad = [(f["name"], r.day) for f in feats for r in f["_rules"] if r.status == "unknown"]
    print("unknown 사유 예시(값 없음/시작=종료):", raw_bad[:6])
    print("평일 종료시각 상위:", Counter(r.close_min // 60 for r in all_rules if r.day == 2 and r.status == "open").most_common(8))
    print("평일 자정 넘김 약국:", len({r.ref for r in all_rules if r.day == 2 and r.status == "open" and r.close_min > 1440}))
    print("\n시각별 영업 약국 수 (open / unknown):")
    for label in ref_days:
        print(f"  {label} {ref_days[label]}: " + "  ".join(
            f"{r['at'][11:13]}시 {r['open']}/{r['unknown']}" for r in rows if r["day"] == label))


if __name__ == "__main__":
    main()
