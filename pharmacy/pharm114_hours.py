"""pharm114(휴일지킴이약국) 상세 API 응답 → hours.Rule 목록. 교차검증용 출처.

해석 원칙 (AllThePlaces 관례 준용)
  - *OperationType 문자열보다 리스트의 실제 모양을 우선한다 (type 값이 ''/None/불일치인 레코드가 많다).
  - 명시적 휴무(type NOT, isOpen N)만 closed. 근거 없는 요일은 unknown — 휴무로 추정하지 않는다.
  - "00:00-00:00", 시작=종료 구간은 24시간이 아니라 무효로 본다.
  - midnightList(심야당번)는 시작시각이 없으므로 같은 요일 정규 구간의 종료를 연장하는 데만 쓴다.
  - holidayList는 '다가오는 공휴일 1~2건'뿐인 날짜별 데이터라 정규 규칙과 분리한다.
"""

from __future__ import annotations

from common.hours import Rule, interval, to_min


def _entry_rule(ref, day, wom, e, inferred, source) -> Rule:
    if e.get("isOpen") == "N":
        return Rule(ref, day, wom, "closed", inferred=inferred, source=source)
    iv = interval(e.get("startTime"), e.get("endTime"))
    if iv is None:
        return Rule(ref, day, wom, "unknown", inferred=inferred, source=source)
    return Rule(ref, day, wom, "open", iv[0], iv[1], inferred, source)


def _weekday_rules(ref, typ, lst) -> list[Rule]:
    days = range(5)
    if typ == "NOT":
        return [Rule(ref, d, 0, "closed", source="weekday") for d in days]
    inferred = typ not in ("SAME_TIME", "SPECIFIC_TIME")
    if not lst:
        return [Rule(ref, d, 0, "unknown", source="weekday") for d in days]
    by_week = {e["week"]: e for e in lst}
    if len(lst) == 1 or (typ == "SAME_TIME" and 0 in by_week):
        e = by_week.get(0, lst[0])
        return [_entry_rule(ref, d, 0, e, inferred, "weekday") for d in days]
    return [
        _entry_rule(ref, d, 0, by_week[d], inferred, "weekday") if d in by_week
        else Rule(ref, d, 0, "unknown", inferred=inferred, source="weekday")
        for d in days
    ]


def _weekend_rules(ref, day, typ, lst, source) -> list[Rule]:
    if typ == "NOT":
        return [Rule(ref, day, 0, "closed", source=source)]
    if not lst:
        return [Rule(ref, day, 0, "unknown", source=source)]
    inferred = typ not in ("EVERY_WEEK", "SPECIFIC_WEEK", "SAME_TIME", "SPECIFIC_TIME")
    nth = {e["weekNum"]: e for e in lst if 1 <= (e.get("weekNum") or 0) <= 5}
    # SPECIFIC_WEEK(또는 type 미기재) + weekNum 1..5 → n번째 주 규칙
    if nth and (typ == "SPECIFIC_WEEK" or inferred):
        return [_entry_rule(ref, day, n, nth[n], inferred, source) for n in sorted(nth)]
    # 그 외(EVERY_WEEK 등)는 매주 규칙: weekNum 0 항목 우선
    e = next((x for x in lst if not x.get("weekNum")), lst[0])
    return [_entry_rule(ref, day, 0, e, inferred, source)]


def _apply_midnight(rules: list[Rule], typ, lst) -> None:
    """심야당번 종료시각으로 같은 요일 정규 구간을 연장 (짧아지게는 하지 않음)."""
    if not lst or typ == "NOT":
        return
    if typ == "SAME_TIME" or len(lst) == 1:
        ends = {d: lst[0] for d in range(5)}
    else:
        ends = {e["week"]: e for e in lst if e.get("week") is not None and 0 <= e["week"] <= 6}
    for r in rules:
        e = ends.get(r.day)
        if not e or e.get("isOpen") != "Y" or r.status != "open":
            continue
        end = to_min(e.get("endTime"))
        if end is None:
            continue
        if end < r.open_min:  # "01:00"처럼 24h 미만 표기
            end += 1440
        if r.close_min < end <= r.open_min + 1440:
            r.close_min, r.source = end, r.source + "+midnight"


def parse_info(d: dict) -> tuple[list[Rule], list[dict]]:
    """getPharmInfo 응답 1건 → (정규 규칙, 날짜별 공휴일 예외)."""
    p = d["pharmInfo"]
    ref = p["pharmacyCode"]
    rules = _weekday_rules(ref, p.get("weekdayOperationType"), d.get("weekdayList") or [])
    rules += _weekend_rules(ref, 5, p.get("saturdayOperationType"), d.get("saturdayList") or [], "saturday")
    rules += _weekend_rules(ref, 6, p.get("sundayOperationType"), d.get("sundayList") or [], "sunday")
    _apply_midnight(rules, p.get("midnightOperationType"), d.get("midnightList") or [])

    holidays = []
    for e in (d.get("holidayList") or []) + (d.get("singleHolidayList") or []):
        if not e.get("date"):
            continue
        iv = interval(e.get("startTime"), e.get("endTime"))
        holidays.append({
            "ref": ref, "date": e["date"], "name": e.get("dateName"),
            "status": "closed" if e.get("isOpen") == "N" else ("open" if iv else "open_hours_unknown"),
            "open_min": iv[0] if iv else None, "close_min": iv[1] if iv else None,
        })
    return rules, holidays
