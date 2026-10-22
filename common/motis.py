"""MOTIS HTTP API 클라이언트 (127.0.0.1:8080). 서버는 data/motis 에서 `motis server`로 띄운다."""

import json
import time
import urllib.error
import urllib.parse as U
import urllib.request

BASE = "http://127.0.0.1:8080"


def _get(path: str, params: dict, timeout: float = 120, retries: int = 3):
    url = f"{BASE}{path}?{U.urlencode(params)}"
    for attempt in range(retries):
        try:
            return json.load(urllib.request.urlopen(url, timeout=timeout))
        except urllib.error.HTTPError:
            raise
        except Exception:  # noqa: BLE001 - 일시적 연결 오류는 재시도
            if attempt == retries - 1:
                raise
            time.sleep(1 + attempt)


def one_to_all(lat: float, lon: float, time_utc: str, max_minutes: int) -> list[tuple[str, int]]:
    """출발점에서 max_minutes 안에 닿는 정류장: [(stopId, 분), …]."""
    r = _get("/api/v6/one-to-all", dict(one=f"{lat},{lon}", time=time_utc, maxTravelTime=max_minutes))
    return [(x["place"].get("stopId"), x["duration"]) for x in r["all"]]


def walk_one_to_many(one: tuple[float, float], many: list[tuple[float, float]], max_seconds: int,
                     arrive_by: bool) -> list[float | None]:
    """도보 one-to-many (초). 닿지 못하면 None. arrive_by=True면 many → one 방향."""
    r = _get("/api/v1/one-to-many", dict(
        one=f"{one[0]};{one[1]}", many=",".join(f"{a};{b}" for a, b in many), mode="WALK",
        max=max_seconds, maxMatchingDistance=250, arriveBy=str(arrive_by).lower()))
    return [x.get("duration") for x in r]


def trip_lines(trip_id: str) -> list[list[tuple[float, float]]]:
    """운행 궤적(구간별 [(lon, lat), …]). 없는 운행이면 빈 목록."""
    try:
        r = _get("/api/v6/trip", dict(tripId=trip_id), timeout=60)
    except urllib.error.HTTPError:
        return []
    lines = [decode_polyline(leg["legGeometry"]["points"], leg["legGeometry"].get("precision", 6))
             for leg in r.get("legs", []) if leg.get("legGeometry", {}).get("points")]
    return [ln for ln in lines if len(ln) >= 2]


def decode_polyline(s: str, precision: int = 6) -> list[tuple[float, float]]:
    """Google encoded polyline → [(lon, lat), …]."""
    pts, idx, lat, lon, f = [], 0, 0, 0, 10 ** precision
    while idx < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += d
            else:
                lon += d
        pts.append((lon / f, lat / f))
    return pts
