"""[교차검증 출처] pharm114 원본(pharmacy.pharm114_collect) → 분석용 테이블 + 품질 리포트.

  python -m pharmacy.pharm114_build --ref-date 2026-10-26

산출물 (data/pharm114/):
  pharmacies.csv / pharmacies.geojson  약국 1행 (ATP 필드명 준용: ref, name, addr_full, lat, lon, phone, opening_hours …)
  hours_rules.csv                      정규 영업 규칙 (common.hours.Rule, 약국×요일×n번째주)
  holiday_exceptions.csv               날짜별 공휴일 예외 (다가오는 공휴일만 제공됨)
  open_by_hour.csv                     기준일별(평일/토/일) 매시 정각 영업 약국 수 (open/closed/unknown)
"""

import argparse
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path

from common.hours import as_opening_hours, status_at
from common.paths import PHARM114
from pharmacy.pharm114_hours import parse_info

SUSPECT_CLOSE = 29 * 60 + 30
SEOUL_BBOX = (37.40, 37.72, 126.73, 127.20)  # lat_min, lat_max, lon_min, lon_max (경계 여유 포함)


def load(data: Path):
    lists = {}
    for f in sorted((data / "raw").glob("list_*.jsonl")):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            lists[r["pharmacyCode"]] = r
    infos = {}
    for code in lists:
        f = data / "raw" / "info" / f"{code}.json"
        if f.exists():
            infos[code] = json.loads(f.read_text(encoding="utf-8"))
    return lists, infos


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def feature(lst: dict, info: dict, rules) -> dict:
    p = info["pharmInfo"]
    # 주의: API 필드명과 달리 coordinateX = 위도, coordinateY = 경도
    lat, lon = to_float(p.get("coordinateX") or lst.get("coordinateX")), to_float(p.get("coordinateY") or lst.get("coordinateY"))
    upd = [e.get("updateDt") for k in ("weekdayList", "saturdayList", "sundayList") for e in info.get(k) or [] if e.get("updateDt")]
    return {
        "ref": p["pharmacyCode"],
        "name": p.get("employmentName"),
        "addr_full": p.get("employmentAddress1"),
        "addr_detail": p.get("employmentAddress2"),
        "postcode": p.get("employmentZipcode"),
        "phone": p.get("employmentPhone"),
        "lat": lat,
        "lon": lon,
        "sido": lst.get("_sido"),
        "gugun": lst.get("_gugun"),
        "region_code": lst.get("regionCode"),
        # 목록상 구/군과 실제 주소의 시도가 다른 레코드가 있다 (예: 강남구 목록 + 용인 주소). 접근성 분석은 좌표 기준.
        "addr_sido_mismatch": not (p.get("employmentAddress1") or "").startswith(lst.get("_sido") or ""),
        "license_no": p.get("lcnsNo"),
        "medical_no": p.get("medicalNo"),
        "public_midnight": p.get("publicMidnightYn") == "Y",       # 공공심야약국
        "moonlight": lst.get("moonlightPharmacy") == "Y",           # 달빛어린이병원 연계 (목록 API 플래그)
        "ppds": (p.get("ppdsYn") or lst.get("ppdsYn")) == "Y",      # PPDS(의약품 안전사용) 참여
        "brttl_pprmdc": lst.get("brttlPprmdcAt") == "Y",
        "exposure": p.get("exposureYn"),
        "operation_type": p.get("operationType"),
        "weekday_type": p.get("weekdayOperationType"),
        "saturday_type": p.get("saturdayOperationType"),
        "sunday_type": p.get("sundayOperationType"),
        "midnight_type": p.get("midnightOperationType"),
        "hours_inferred": any(r.inferred for r in rules),
        # 종료 29:30(익일 05:30)이 비정상적으로 많고(평범한 주간 약국 포함) 갱신일이 몰려 있어 입력 인공물 의심 → 민감도 분석용
        "suspect_overnight": any(r.status == "open" and r.close_min >= SUSPECT_CLOSE for r in rules),
        "hours_updated": max(upd) if upd else None,
        "opening_hours": as_opening_hours(rules),
        "fetched_at": info.get("_fetched_at"),
    }


def next_weekday(start: dt.date, wd: int) -> dt.date:
    return start + dt.timedelta(days=(wd - start.weekday()) % 7)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=PHARM114)
    ap.add_argument("--ref-date", type=dt.date.fromisoformat, default=dt.date.today(),
                    help="open_by_hour 기준 주 (이 날짜 이후 첫 수/토/일). 공휴일이 끼지 않은 주를 고를 것")
    a = ap.parse_args()

    lists, infos = load(a.data)
    feats, all_rules, holidays, missing = [], [], [], []
    for code, lst in lists.items():
        info = infos.get(code)
        if not info or not info.get("pharmInfo"):
            missing.append(code)
            continue
        rules, hol = parse_info(info)
        f = feature(lst, info, rules)
        f["_rules"] = rules
        feats.append(f)
        all_rules += rules
        holidays += hol

    cols = [k for k in feats[0] if not k.startswith("_")]
    with open(a.data / "pharmacies.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(feats)
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]} if f["lat"] else None,
         "properties": {k: f[k] for k in cols}} for f in feats]}
    (a.data / "pharmacies.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    with open(a.data / "hours_rules.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["ref", "day", "week_of_month", "status", "open_min", "close_min", "inferred", "source"])
        w.writerows([[r.ref, r.day, r.week_of_month, r.status, r.open_min, r.close_min, r.inferred, r.source] for r in all_rules])
    with open(a.data / "holiday_exceptions.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, ["ref", "date", "name", "status", "open_min", "close_min"])
        w.writeheader()
        w.writerows(holidays)

    # ---- 시간대별 영업 약국 수 (평일=수, 토, 일)
    ref_days = {"wed": next_weekday(a.ref_date, 2), "sat": next_weekday(a.ref_date, 5), "sun": next_weekday(a.ref_date, 6)}
    rows = []
    for label, day in ref_days.items():
        for h in list(range(18, 24)) + list(range(0, 7)):
            when = dt.datetime.combine(day + dt.timedelta(days=1 if h < 7 else 0), dt.time(h))
            st = [(f, status_at(f["_rules"], when)) for f in feats]
            c = Counter(s for _, s in st)
            rows.append({"day": label, "date": day.isoformat(), "at": when.isoformat(timespec="minutes"),
                         "open": c["open"], "closed": c["closed"], "unknown": c["unknown"],
                         "open_excl_suspect": sum(s == "open" and not f["suspect_overnight"] for f, s in st),
                         "open_public_midnight": sum(s == "open" and f["public_midnight"] for f, s in st)})
    with open(a.data / "open_by_hour.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # ---- 품질 리포트
    n = len(feats)
    print(f"약국 {n}건 (목록 {len(lists)}, 상세 누락 {len(missing)})")
    print("구별:", dict(sorted(Counter(f["gugun"] for f in feats).items())))
    no_geo = [f for f in feats if f["lat"] is None]
    out_bbox = [f for f in feats if f["lat"] and f["sido"] == "서울" and not (
        SEOUL_BBOX[0] <= f["lat"] <= SEOUL_BBOX[1] and SEOUL_BBOX[2] <= f["lon"] <= SEOUL_BBOX[3])]
    print(f"좌표 없음 {len(no_geo)}, 서울 bbox 밖 {len(out_bbox)}", [(f["name"], f["lat"], f["lon"]) for f in out_bbox[:5]])
    print("주소 시도 ≠ 목록 시도:", sum(f["addr_sido_mismatch"] for f in feats))
    print("중복 좌표(동일 지점 3곳 이상):", sum(1 for _, k in Counter((f["lat"], f["lon"]) for f in feats).items() if k >= 3))
    print(f"hours_inferred {sum(f['hours_inferred'] for f in feats)} / opening_hours 빈 문자열 {sum(not f['opening_hours'] for f in feats)}")
    dn = ["월", "화", "수", "목", "금", "토", "일"]
    for d in range(7):
        c = Counter(r.status for r in all_rules if r.day == d and r.week_of_month in (0, 1))
        print(f"  {dn[d]}: open {c['open']} closed {c['closed']} unknown {c['unknown']}")
    print("평일 종료시각 상위:", Counter(r.close_min // 60 for r in all_rules if r.day == 2 and r.status == "open").most_common(8))
    print("자정 넘김 규칙(약국 수):", len({r.ref for r in all_rules if r.status == "open" and r.close_min > 1440}))
    print("공공심야약국:", sum(f["public_midnight"] for f in feats), "/ 달빛:", sum(f["moonlight"] for f in feats))
    print("suspect_overnight(종료 ≥29:30):", sum(f["suspect_overnight"] for f in feats))
    print("\n시각별 영업 약국 수 (open / open_excl_suspect / 공공심야 / unknown):")
    for label in ref_days:
        print(f"  {label} {ref_days[label]}: " + "  ".join(
            f"{r['at'][11:13]}시 {r['open']}/{r['open_excl_suspect']}/{r['open_public_midnight']}/{r['unknown']}"
            for r in rows if r["day"] == label))


if __name__ == "__main__":
    main()
