"""영업시간 규칙 모델과 특정 시각 영업 여부 판정 (출처 공통).

규칙 1행 = Rule(ref, day, week_of_month, status, open_min, close_min, inferred, source)
  day            : 0=월 … 6=일 (datetime.weekday()와 동일), 7=공휴일(PH)
  week_of_month  : 0=매주, n=그 달의 n번째 해당 요일 (격주 영업 표현)
  status         : "open" | "closed" | "unknown"
  open/close_min : 그 날 0시 기준 분. 자정 넘김은 1440 초과 (예: 13:00-25:00 → 780, 1500)
  inferred       : 출처에 명시가 없어 모양으로 해석한 경우 True
  source         : 어느 출처·필드에서 왔는지

출처별 파서: pharmacy.egen_build.parse_egen (기준), pharmacy.pharm114_hours.parse_info (교차검증),
            pharmacy.seoul_csv_build.parse_row (교차검증)
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

DAY_ABBR = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
PH = 7  # 공휴일 규칙의 day 값
_TIME = re.compile(r"^\s*(\d{1,2}):?(\d{2})\s*$")


@dataclass
class Rule:
    ref: str
    day: int
    week_of_month: int
    status: str
    open_min: int | None = None
    close_min: int | None = None
    inferred: bool = False
    source: str = ""


def to_min(t: str | None) -> int | None:
    if not t:
        return None
    m = _TIME.match(t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if mi >= 60 or h > 48:
        return None
    return h * 60 + mi


def interval(start: str | None, end: str | None) -> tuple[int, int] | None:
    """유효한 (open, close) 분 구간. 자정 넘김은 close에 1440을 더한다. 무효면 None."""
    a, b = to_min(start), to_min(end)
    if a is None or b is None or a == b:
        return None
    if b < a:  # "22:00"-"02:00" 처럼 24h 미만 표기로 자정을 넘긴 경우
        b += 1440
    if b - a > 1440:
        return None
    return a, b


# ---------------------------------------------------------------- 직렬화

def _hhmm(m: int) -> str:
    m %= 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def as_opening_hours(rules: list[Rule]) -> str:
    """OSM opening_hours 문자열 (참고용; 분석에는 rules 테이블을 쓴다). unknown 요일은 생략."""
    parts: list[tuple[str, str]] = []
    for day in range(7):
        rs = [r for r in rules if r.day == day and r.status != "unknown"]
        for r in sorted(rs, key=lambda r: r.week_of_month):
            sel = DAY_ABBR[day] + (f"[{r.week_of_month}]" if r.week_of_month else "")
            val = "off" if r.status == "closed" else f"{_hhmm(r.open_min)}-{_hhmm(r.close_min)}"
            if val == "00:00-00:00" and r.close_min - r.open_min == 1440:
                val = "00:00-24:00"
            parts.append((sel, val))
    # 연속된 요일(매주 규칙)이 같은 값이면 Mo-Fr 식으로 묶는다
    out, i = [], 0
    while i < len(parts):
        sel, val = parts[i]
        j = i
        while (j + 1 < len(parts) and "[" not in sel and "[" not in parts[j + 1][0]
               and parts[j + 1][1] == val and DAY_ABBR.index(parts[j + 1][0]) == DAY_ABBR.index(parts[j][0]) + 1):
            j += 1
        sel = sel if j == i else f"{sel}-{parts[j][0]}"
        out.append(f"{sel} {val}")
        i = j + 1
    ph = next((r for r in rules if r.day == PH and r.status != "unknown"), None)
    if ph:
        out.append("PH " + ("off" if ph.status == "closed" else f"{_hhmm(ph.open_min)}-{_hhmm(ph.close_min)}"))
    return "; ".join(out)


# ---------------------------------------------------------------- 질의

def week_of_month(d: dt.date) -> int:
    """그 달의 n번째 해당 요일 (1~5)."""
    return (d.day - 1) // 7 + 1


def rule_for(rules_by_day: dict[int, list[Rule]], date: dt.date, holidays: frozenset = frozenset()) -> Rule | None:
    if date in holidays and PH in rules_by_day:
        return rules_by_day[PH][0]
    rs = rules_by_day.get(date.weekday(), [])
    n = week_of_month(date)
    return next((r for r in rs if r.week_of_month == n), None) or next((r for r in rs if r.week_of_month == 0), None)


def status_at(rules: list[Rule], when: dt.datetime, unknown_as: str = "unknown",
              holidays: frozenset = frozenset()) -> str:
    """when 시각의 영업 상태: open | closed | unknown.
    전날 규칙이 자정을 넘겨 이어지는 경우(예: 월 13:00-25:00 → 화 00:00-01:00)도 반영한다.
    unknown_as="closed"로 주면 unknown 요일을 휴무로 간주(민감도 분석용).
    holidays에 든 날짜는 공휴일(day=PH) 규칙이 있으면 그것을 쓴다."""
    by_day: dict[int, list[Rule]] = {}
    for r in rules:
        by_day.setdefault(r.day, []).append(r)
    t = when.hour * 60 + when.minute
    today = rule_for(by_day, when.date(), holidays)
    yesterday = rule_for(by_day, when.date() - dt.timedelta(days=1), holidays)
    if yesterday and yesterday.status == "open" and yesterday.close_min > 1440 and t < yesterday.close_min - 1440:
        return "open"
    if today is None or today.status == "unknown":
        return unknown_as
    if today.status == "open" and today.open_min <= t < today.close_min:
        return "open"
    return "closed"
