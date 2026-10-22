"""접근성 계산 결과 → 웹 지도(web/)용 데이터.

web/data/meta.json        격자 크기·모서리 좌표(MapLibre canvas source용)·시점 목록·통계
web/data/frames.bin       uint8 [n_frames][rows*cols] 분 (255 = 60분 초과 또는 격자 밖)
web/data/nearest.bin      int16 [n_frames][rows*cols] 가장 가까운 영업 약국 index (-1 없음)
web/data/mask.bin         uint8 [rows*cols] 2=서울 안, 1=경계 밖 버퍼, 0=격자 없음
web/data/pharmacies.json  [[lon, lat, open_min, close_min, spill_min, name, thu_open, thu_close], …]  (18:00 기준 분, 영업 판정용)
web/data/seoul.geojson    서울 경계

아직 계산되지 않은 시점은 frames에서 빠진다 (meta.times에 있는 시점만).

  python -m transit.pack_web            (기본 MOTIS 결과, --engine r5 로 R5 검증 결과)
"""

import argparse
import datetime as dt
import json
import shutil
import sys

import numpy as np
import pandas as pd
from pyproj import Transformer

from common.paths import ACCESS_MOTIS, ACCESS_R5, GRID, SEOUL_BOUNDARY
from common.paths import WEB_DATA as WEB
from common.pharmacies import load_rows
from common.timeline import MAX_TT, STEPS, T0, minutes_since_t0
from pharmacy.egen_build import parse_egen

BANDS = [3, 5, 10, 15, 30]   # 등시간 띠(분): 생활SOC 기준 도보권
R5_STEPS = [T0 + dt.timedelta(minutes=5 * k) for k in range(97)]   # R5 검증판: 18:00~02:00, 5분 간격


def pharmacy_intervals(rule_sets) -> list:
    """수요일 기준 18:00부터의 분으로 [open, close) 와 전날(화) 자정 넘김 종료(spill) — 웹 필터용."""
    out = []
    for rules in rule_sets:
        wed = next((r for r in rules if r.day == 2 and r.week_of_month == 0), None)
        tue = next((r for r in rules if r.day == 1 and r.week_of_month == 0), None)
        o = c = None
        if wed and wed.status == "open":
            o, c = wed.open_min - 18 * 60, wed.close_min - 18 * 60
        # 수요일 낮 영업은 18:00 이전에 시작 → 음수 open. 화요일 자정 넘김은 수요일 새벽이라 18~02시 창과 무관.
        # 목요일 새벽(=수요일 영업의 자정 넘김)은 close > 360(=익일 00:00)으로 이미 표현됨.
        spill = None if not (tue and tue.status == "open" and tue.close_min > 1440) else tue.close_min - 1440 - 18 * 60
        # 목요일 아침 영업 (06:00 전에 여는 약국이 슬라이더 끝에서 켜지도록): 수 18:00 기준 분
        thu = next((r for r in rules if r.day == 3 and r.week_of_month == 0), None)
        o2 = c2 = None
        if thu and thu.status == "open":
            o2, c2 = thu.open_min + 1440 - 18 * 60, thu.close_min + 1440 - 18 * 60
        out.append((o, c, spill, o2, c2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", type=int, default=250)
    ap.add_argument("--engine", choices=["motis", "r5"], default="motis",
                    help="motis: 10분 간격(transit.compute_access, 기본) / r5: 5분 간격(transit.r5.compute_access_r5)")
    a = ap.parse_args()
    steps = STEPS if a.engine == "motis" else R5_STEPS
    src = ACCESS_MOTIS if a.engine == "motis" else ACCESS_R5
    WEB.mkdir(parents=True, exist_ok=True)

    grid = pd.read_parquet(GRID)
    rows, cols = int(grid.row.max()) + 1, int(grid.col.max()) + 1
    flat = (grid.row * cols + grid.col).to_numpy()
    mask = np.zeros(rows * cols, np.uint8)
    mask[flat] = np.where(grid.in_seoul, 2, 1)

    times, frames, nears = [], [], []
    for t in steps:
        f = src / f"t_{t:%H%M}.npz"
        if not f.exists():
            continue
        z = np.load(f)
        fr = np.full(rows * cols, 255, np.uint8)
        nr = np.full(rows * cols, -1, np.int16)
        fr[flat] = z["minutes"][grid.id.to_numpy()]
        nr[flat] = z["nearest"][grid.id.to_numpy()]
        times.append(minutes_since_t0(t))
        frames.append(fr)
        nears.append(nr)
    if not frames:
        sys.exit("계산된 시점이 아직 없습니다")
    np.stack(frames).tofile(WEB / "frames.bin")
    np.stack(nears).tofile(WEB / "nearest.bin")
    mask.tofile(WEB / "mask.bin")

    # 격자 셀 경계의 네 모서리 (EPSG:5179 → 4326). 행 0 = 남쪽(y 최소)
    x0 = grid.x.min() - a.cell / 2
    y0 = grid.y.min() - a.cell / 2
    x1, y1 = x0 + cols * a.cell, y0 + rows * a.cell
    tr = Transformer.from_crs(5179, 4326, always_xy=True)
    corners = [list(tr.transform(x, y)) for x, y in [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]]  # 좌상·우상·우하·좌하

    # 서울 안 격자 기준 시점별 띠 비율
    inside = mask == 2
    stats = [[float(np.mean(fr[inside] <= b)) for b in BANDS] for fr in frames]

    rows_ph = load_rows()
    iv = pharmacy_intervals([parse_egen(r) for r in rows_ph])
    ph = [[round(float(r["LON"]), 6), round(float(r["LAT"]), 6), o, c, s, r.get("TITLE"), o2, c2]
          for r, (o, c, s, o2, c2) in zip(rows_ph, iv)]
    (WEB / "pharmacies.json").write_text(json.dumps(ph, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.copy(SEOUL_BOUNDARY, WEB / "seoul.geojson")

    meta = {"rows": rows, "cols": cols, "cell": a.cell, "corners": corners, "times": times,
            "t0": T0.strftime("%Y-%m-%d %H:%M"), "bands": BANDS, "stats": stats, "max_tt": MAX_TT,
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "n_total_steps": len(steps), "engine": a.engine}
    (WEB / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"시점 {len(times)}/{len(steps)}, 격자 {cols}×{rows}, frames {(WEB / 'frames.bin').stat().st_size / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
