"""E-Gen 약국 데이터(collect_egen.py) → 분석 테이블 + 품질 리포트 + pharm114 교차검증. (기본 source)

입력: data/egen/egen_pharmacies.jsonl  (EMOGCODE = 서울시 CSV 약국ID)
  요일별 {MON..SAT,SUN,HOL}_STT_TME / _END_TME  "HHMM" (2400 초과 = 익일)
  SUN_{FRT,SCD,THI,FUR,FIF}_YON  일요일 1~5번째 주 운영 여부 (Y/N)
  HOLIDAYYN                      공휴일 운영 여부 (Y면 HOL_* 시간이 있음)

해석 (E-Gen 관례 — 데이터로 확인):
  - 시간 없음 = 그날 운영 안 함. 일요일 시간 없음 ⇔ 주차 플래그 NNNNN, 공휴일 시간 없음 ⇔ HOLIDAYYN=N 이
    서울 5,539곳에서 예외 없이 일치한다. 평일·토도 같은 관례로 closed 처리하되 inferred=True로 표시한다.
  - 일요일: 시간이 있고 플래그가 일부 주만 Y면 n번째 주 규칙(week_of_month=n)으로 만든다. 전부 Y면 매주 규칙.
  - 공휴일: day=PH(7) 규칙. 분석 시 status_at(..., holidays={공휴일 날짜})로 쓴다.

산출물 (data/egen/):
  pharmacies.csv / .geojson   서울 + 인접 경기·인천 (in_seoul 열; 경계효과용으로 같이 둔다)
  hours_rules.csv             hours.Rule 형식
  open_by_hour.csv            서울 약국만, 기준 주의 수/토/일 + 공휴일 가정 18시~익일 06시
  crosscheck_p114.csv         link.csv로 매칭된 약국의 E-Gen vs pharm114 수요일·일요일 비교

  python -m pharmacy.egen_build --ref-date 2026-10-26
"""

import argparse
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from common.hours import PH, Rule, as_opening_hours, interval, status_at
from common.paths import EGEN, EGEN_JSONL, LINK_CSV, PHARM114

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT"]
SUN_FLAGS = ["SUN_FRT_YON", "SUN_SCD_YON", "SUN_THI_YON", "SUN_FUR_YON", "SUN_FIF_YON"]  # 1~5번째 일요일
LOCA = {"11": "서울", "21": "경기", "14": "인천"}  # EMOGLOCA (응답에서 확인한 값)


def hhmm(v) -> str | None:
    if not v or not str(v).strip():
        return None
    v = str(v).strip().zfill(4)
    return f"{v[:-2]}:{v[-2:]}"


def _day_rule(ref, day, r, key, source="egen") -> Rule:
    s, e = r.get(f"{key}_STT_TME"), r.get(f"{key}_END_TME")
    if not s and not e:
        return Rule(ref, day, 0, "closed", inferred=day < 6, source=source + ":absent")
    iv = interval(hhmm(s), hhmm(e))
    if iv is None:
        return Rule(ref, day, 0, "unknown", source=source + ":invalid")
    return Rule(ref, day, 0, "open", iv[0], iv[1], source=source)


def parse_egen(r: dict) -> list[Rule]:
    ref = r["EMOGCODE"]
    rules = [_day_rule(ref, d, r, key) for d, key in enumerate(DAYS)]

    # 일요일: 시간 + 주차 플래그
    sun = _day_rule(ref, 6, r, "SUN")
    flags = "".join((r.get(k) or "-") for k in SUN_FLAGS)
    if sun.status == "open" and "N" in flags and "Y" in flags:
        for n, f in enumerate(flags, 1):
            if f == "Y":
                rules.append(Rule(ref, 6, n, "open", sun.open_min, sun.close_min, source="egen:sun_week"))
            elif f == "N":
                rules.append(Rule(ref, 6, n, "closed", source="egen:sun_week"))
            else:
                rules.append(Rule(ref, 6, n, "unknown", source="egen:sun_week"))
    else:
        if sun.status == "open" and "Y" not in flags and "N" in flags:
            sun.inferred, sun.source = True, "egen:hours_but_NNNNN"  # 시간은 있는데 플래그 전부 N (2건) → 시간 우선
        rules.append(sun)

    # 공휴일
    if r.get("HOLIDAYYN") == "N":
        rules.append(Rule(ref, PH, 0, "closed", source="egen:HOLIDAYYN"))
    else:
        rules.append(_day_rule(ref, PH, r, "HOL"))
    return rules


def next_weekday(start: dt.date, wd: int) -> dt.date:
    return start + dt.timedelta(days=(wd - start.weekday()) % 7)


def crosscheck(feats: dict, out: Path, ref_days: dict) -> pd.DataFrame:
    """pharm114(link.csv 매칭분)와 같은 날짜·시각의 영업 상태 비교. pharm114는 교차검증용으로만 쓴다."""
    link_f, p114_f = LINK_CSV, PHARM114 / "hours_rules.csv"
    if not (link_f.exists() and p114_f.exists()):
        print("(교차검증 건너뜀: data/crosscheck/link.csv 또는 data/pharm114/hours_rules.csv 없음)")
        return pd.DataFrame()
    link = pd.read_csv(link_f, dtype=str)
    p114: dict[str, list[Rule]] = {}
    for r in pd.read_csv(p114_f, dtype=str).itertuples():
        p114.setdefault(r.ref, []).append(Rule(
            r.ref, int(r.day), int(r.week_of_month), r.status,
            int(r.open_min) if pd.notna(r.open_min) else None, int(r.close_min) if pd.notna(r.close_min) else None))
    probes = {"wed_10": dt.datetime.combine(ref_days["wed"], dt.time(10)),
              "wed_22": dt.datetime.combine(ref_days["wed"], dt.time(22)),
              "sun_12": dt.datetime.combine(ref_days["sun"], dt.time(12)),
              "sun_20": dt.datetime.combine(ref_days["sun"], dt.time(20))}
    rows = []
    for lk in link.itertuples():
        f = feats.get(lk.seoul_id)
        if f is None or lk.p114_ref not in p114:
            continue
        row = {"ref": lk.seoul_id, "p114_ref": lk.p114_ref, "name": f["name"], "link_tier": lk.tier,
               "egen_hours": f["opening_hours"]}
        for k, when in probes.items():
            row[f"{k}_egen"] = status_at(f["_rules"], when)
            row[f"{k}_p114"] = status_at(p114[lk.p114_ref], when)
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(out / "crosscheck_p114.csv", index=False, encoding="utf-8-sig")
    print(f"\n[pharm114 교차검증] 매칭 {len(df)}곳 (p114 unknown 제외한 일치율)")
    for k in probes:
        known = df[df[f"{k}_p114"] != "unknown"]
        agree = (known[f"{k}_egen"] == known[f"{k}_p114"]).mean()
        tab = pd.crosstab(known[f"{k}_egen"], known[f"{k}_p114"]).to_dict()
        print(f"  {k}: 일치 {agree:.1%} (n={len(known)})  egen×p114 {tab}")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=EGEN_JSONL)
    ap.add_argument("--out", type=Path, default=EGEN)
    ap.add_argument("--ref-date", type=dt.date.fromisoformat, default=dt.date.today(),
                    help="기준 주 (이 날짜 이후 첫 수/토/일). 공휴일이 끼지 않은 주를 고를 것")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    feats: dict[str, dict] = {}
    for line in a.src.open(encoding="utf-8"):
        r = json.loads(line)
        rules = parse_egen(r)
        addr = r.get("ADDRROAD") or ""
        feats[r["EMOGCODE"]] = {
            "ref": r["EMOGCODE"],
            "name": r.get("TITLE"),
            "addr_full": addr,
            "phone": r.get("TEL"),
            "lat": float(r["LAT"]) if r.get("LAT") else None,
            "lon": float(r["LON"]) if r.get("LON") else None,
            "sido": LOCA.get(r.get("EMOGLOCA"), r.get("EMOGLOCA")),
            "gugun": addr.split()[1] if len(addr.split()) > 1 else None,
            "in_seoul": r.get("EMOGLOCA") == "11",
            "opening_hours": as_opening_hours(rules),
            "sunday_weeks": "".join((r.get(k) or "-") for k in SUN_FLAGS),
            "holiday_yn": r.get("HOLIDAYYN"),
            "note": (r.get("OPN_BIGO") or "").replace("\n", " ").strip() or None,
            "egen_updated": r.get("EMOGUPDT"),
            "_rules": rules,
        }
    fl = list(feats.values())

    cols = [k for k in fl[0] if not k.startswith("_")]
    with open(a.out / "pharmacies.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(fl)
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]} if f["lat"] else None,
         "properties": {k: f[k] for k in cols}} for f in fl]}
    (a.out / "pharmacies.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    with open(a.out / "hours_rules.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["ref", "day", "week_of_month", "status", "open_min", "close_min", "inferred", "source"])
        w.writerows([[r.ref, r.day, r.week_of_month, r.status, r.open_min, r.close_min, r.inferred, r.source]
                     for f in fl for r in f["_rules"]])

    # ---- 시간대별 영업 약국 수 (서울만). hol = 수요일을 공휴일로 가정
    seoul = [f for f in fl if f["in_seoul"]]
    ref_days = {"wed": next_weekday(a.ref_date, 2), "sat": next_weekday(a.ref_date, 5), "sun": next_weekday(a.ref_date, 6)}
    scen = [("wed", ref_days["wed"], frozenset()), ("sat", ref_days["sat"], frozenset()),
            ("sun", ref_days["sun"], frozenset()), ("hol(wed)", ref_days["wed"], frozenset({ref_days["wed"]}))]
    rows = []
    for label, day, hol in scen:
        for h in list(range(18, 24)) + list(range(0, 7)):
            when = dt.datetime.combine(day + dt.timedelta(days=1 if h < 7 else 0), dt.time(h))
            c = Counter(status_at(f["_rules"], when, holidays=hol) for f in seoul)
            rows.append({"day": label, "date": day.isoformat(), "at": when.isoformat(timespec="minutes"),
                         "open": c["open"], "closed": c["closed"], "unknown": c["unknown"]})
    with open(a.out / "open_by_hour.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # ---- 품질 리포트
    print(f"약국 {len(fl)}건 (서울 {len(seoul)}, {dict(Counter(f['sido'] for f in fl))}), 좌표 없음 {sum(f['lat'] is None for f in fl)}")
    print("서울 구별:", dict(sorted(Counter(f["gugun"] for f in seoul).items())))
    dn = ["월", "화", "수", "목", "금", "토", "일", "공휴일"]
    sr = [r for f in seoul for r in f["_rules"]]
    for d in list(range(7)) + [PH]:
        refs = {r.ref: r for r in sr if r.day == d and r.week_of_month in (0, 1)}
        c = Counter(r.status for r in refs.values())
        print(f"  {dn[d]}: open {c['open']} closed {c['closed']} unknown {c['unknown']}"
              + (f"  (absent→closed {sum(r.source.endswith(':absent') for r in refs.values())})" if d < 6 else ""))
    print("일요일 주차 패턴(서울, 시간 있는 약국):",
          Counter(f["sunday_weeks"] for f in seoul if any(r.day == 6 and r.status == "open" for r in f["_rules"])).most_common(6))
    print("평일 종료시각 상위:", Counter(r.close_min // 60 for r in sr if r.day == 2 and r.status == "open").most_common(8))
    print("평일 자정 넘김 약국:", len({r.ref for r in sr if r.day == 2 and r.status == "open" and r.close_min > 1440}))
    print("\n시각별 영업 약국 수 (서울, open / unknown):")
    for label, day, _ in scen:
        print(f"  {label} {day}: " + "  ".join(f"{r['at'][11:13]}시 {r['open']}/{r['unknown']}" for r in rows if r["day"] == label))

    crosscheck(feats, a.out, ref_days)


if __name__ == "__main__":
    main()
