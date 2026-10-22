"""영업시간 파서·판정 엣지 케이스 테스트 (실제 응답에서 관찰된 모양 기준).  python -m tests.test_hours 또는 pytest"""

import datetime as dt

from common.hours import as_opening_hours, interval, status_at
from pharmacy.pharm114_hours import parse_info


def info(wd_type="SAME_TIME", wd=None, sa_type="NOT", sa=None, su_type="NOT", su=None, mn_type="NOT", mn=None):
    return {
        "pharmInfo": {"pharmacyCode": "P1", "weekdayOperationType": wd_type, "saturdayOperationType": sa_type,
                      "sundayOperationType": su_type, "midnightOperationType": mn_type},
        "weekdayList": wd or [], "saturdayList": sa or [], "sundayList": su or [], "midnightList": mn or [],
        "holidayList": [{"date": "2026-12-25", "dateName": "크리스마스", "isOpen": "N", "startTime": None, "endTime": None}],
    }


def e(start, end, is_open="Y", week=0, week_num=0):
    return {"startTime": start, "endTime": end, "isOpen": is_open, "week": week, "weekNum": week_num}


WED = dt.date(2026, 10, 21)  # 수
SAT2 = dt.date(2026, 10, 10)  # 10월 두 번째 토요일
SAT3 = dt.date(2026, 10, 17)


def at(d, h, m=0):
    return dt.datetime.combine(d, dt.time(h, m))


def test_interval_past_midnight_notation():
    assert interval("13:00", "25:00") == (780, 1500)
    assert interval("22:00", "02:00") == (1320, 1560)
    assert interval("00:00", "00:00") is None  # 24시간으로 보지 않음
    assert interval("09:00", None) is None


def test_same_time_weekday_and_overnight_spill():
    rules, hol = parse_info(info(wd=[e("13:00", "25:00")]))
    assert status_at(rules, at(WED, 23, 30)) == "open"
    assert status_at(rules, at(WED + dt.timedelta(days=1), 0, 30)) == "open"  # 목 00:30 = 수요일 규칙의 연장
    assert status_at(rules, at(WED + dt.timedelta(days=1), 1, 0)) == "closed"
    # 금요일 규칙이 토 00:30까지 이어지지만, 토요일 자체는 NOT(휴무)
    assert status_at(rules, at(dt.date(2026, 10, 24), 0, 30)) == "open"
    assert status_at(rules, at(dt.date(2026, 10, 24), 12)) == "closed"
    assert hol[0]["status"] == "closed"


def test_missing_sunday_is_unknown_not_closed():
    rules, _ = parse_info(info(su_type="", su=[]))
    assert status_at(rules, at(dt.date(2026, 10, 25), 12)) == "unknown"
    assert status_at(rules, at(dt.date(2026, 10, 25), 12), unknown_as="closed") == "closed"


def test_specific_week_saturday():
    sa = [e("09:00", "13:00", "Y" if n in (2, 4) else "N", week_num=n) for n in range(1, 6)]
    rules, _ = parse_info(info(wd=[e("09:00", "18:00")], sa_type="SPECIFIC_WEEK", sa=sa))
    assert status_at(rules, at(SAT2, 10)) == "open"
    assert status_at(rules, at(SAT3, 10)) == "closed"
    assert "Sa[2] 09:00-13:00" in as_opening_hours(rules)


def test_every_week_with_stale_closed_entries():
    # EVERY_WEEK인데 weekNum 0..4 전부 N → 매주 휴무 (관찰된 모양)
    sa = [e("00:00", "00:00", "N", week_num=n) for n in range(5)]
    rules, _ = parse_info(info(wd=[e("09:00", "18:00")], sa_type="EVERY_WEEK", sa=sa))
    assert status_at(rules, at(SAT2, 10)) == "closed"


def test_specific_time_weekday_with_closed_day():
    wd = [e("09:00", "18:00", "N" if d == 2 else "Y", week=d) for d in range(5)]
    rules, _ = parse_info(info(wd_type="SPECIFIC_TIME", wd=wd))
    assert status_at(rules, at(WED, 10)) == "closed"
    assert status_at(rules, at(WED - dt.timedelta(days=1), 10)) == "open"


def test_midnight_extends_weekday_close():
    rules, _ = parse_info(info(wd=[e("09:00", "21:00")], mn_type="SAME_TIME", mn=[e(None, "25:00")]))
    assert status_at(rules, at(WED, 23)) == "open"
    assert status_at(rules, at(WED + dt.timedelta(days=1), 0, 59)) == "open"
    assert as_opening_hours(rules).startswith("Mo-Fr 09:00-01:00")


def test_empty_type_inferred_from_shape():
    rules, _ = parse_info(info(wd_type="", wd=[e("09:00", "19:00")]))
    assert all(r.inferred for r in rules if r.day < 5)
    assert status_at(rules, at(WED, 18, 30)) == "open"


def test_weekday_not_is_closed():
    rules, _ = parse_info(info(wd_type="NOT"))
    assert status_at(rules, at(WED, 10)) == "closed"


def egen(**kw):
    r = {"EMOGCODE": "C1", "HOLIDAYYN": "N", **{f"SUN_{k}_YON": "N" for k in ("FRT", "SCD", "THI", "FUR", "FIF")}}
    for d in ("MON", "TUE", "WED", "THU", "FRI"):
        r[f"{d}_STT_TME"], r[f"{d}_END_TME"] = "0900", "1900"
    r.update(kw)
    return r


def test_egen_absent_day_is_closed_and_week_flags():
    from pharmacy.egen_build import parse_egen
    rules = parse_egen(egen(SUN_STT_TME="1000", SUN_END_TME="1800",
                            SUN_FRT_YON="Y", SUN_THI_YON="Y", SUN_FIF_YON="Y"))
    sun1, sun2 = dt.date(2026, 11, 1), dt.date(2026, 11, 8)  # 11월 첫째·둘째 일요일
    assert status_at(rules, at(sun1, 12)) == "open"
    assert status_at(rules, at(sun2, 12)) == "closed"
    assert status_at(rules, at(dt.date(2026, 10, 31), 12)) == "closed"  # 토 시간 없음 → 휴무
    assert "Su[1] 10:00-18:00" in as_opening_hours(rules)


def test_egen_holiday_and_overnight():
    from pharmacy.egen_build import parse_egen
    rules = parse_egen(egen(WED_END_TME="2600", HOLIDAYYN="Y", HOL_STT_TME="0900", HOL_END_TME="1300"))
    assert status_at(rules, at(WED + dt.timedelta(days=1), 1, 30)) == "open"  # 수 09:00-익일 02:00
    assert status_at(rules, at(WED, 15), holidays=frozenset({WED})) == "closed"
    assert status_at(rules, at(WED, 10), holidays=frozenset({WED})) == "open"
    closed_ph = parse_egen(egen())
    assert status_at(closed_ph, at(WED, 10), holidays=frozenset({WED})) == "closed"


if __name__ == "__main__":  # pytest 없이도 실행 가능
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} passed")
