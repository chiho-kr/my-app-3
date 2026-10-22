"""분석 기준 시각. KTDB GTFS가 평일 하루 시간표라서 기준일은 평일(수)로 고정한다."""

import datetime as dt

DAY = dt.date(2026, 10, 28)                          # 기준 평일 (수)
T0 = dt.datetime.combine(DAY, dt.time(18, 0))        # 시간창 시작 18:00
KST = dt.timezone(dt.timedelta(hours=9))

STEP_MIN = 10
N_STEPS = 73                                         # 18:00 … 익일 06:00, 10분 간격
STEPS = [T0 + dt.timedelta(minutes=STEP_MIN * k) for k in range(N_STEPS)]
WINDOW_MIN = STEP_MIN * (N_STEPS - 1)                # 720분

OPEN_MATRIX_MIN = 780                                # 영업 여부 행렬 길이: 18:00부터 13시간 (06:00 + 최대 60분 이동)
MAX_TT = 60                                          # 최대 이동시간(분), 넘으면 255


def minutes_since_t0(t: dt.datetime) -> int:
    return int((t - T0).total_seconds() // 60)


def to_utc_iso(t: dt.datetime) -> str:
    """KST(naive) → MOTIS가 받는 UTC ISO 문자열."""
    return t.replace(tzinfo=KST).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
