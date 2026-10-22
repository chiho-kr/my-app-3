"""E-Gen(국립중앙의료원 응급의료포털) 약국 찾기 수집기.

Source: https://www.e-gen.or.kr/egen/search_pharmacy.do 가 호출하는 JSON
  POST /egen/retrieve_pharmacy_list.do  (lat, lon, ...)
  - radius 파라미터는 무시되고, 중심에서 약 10km 이내 전체가 한 번에 온다 (paging은 화면용).
  - 그래서 서울을 덮는 격자 중심점들로 호출해 EMOGCODE로 합친다.
  - EMOGCODE = 서울시 열린데이터 「서울시 약국 운영시간 정보」의 약국ID (같은 체계, 정확 조인 가능).

산출물: data/egen/raw_{lat}_{lon}.json (원본), data/egen/egen_pharmacies.jsonl (EMOGCODE 기준 합본)

  python -m pharmacy.egen_collect
"""

import argparse
import json
import time

import numpy as np
import requests
import truststore

# e-gen.or.kr은 중간 인증서를 보내지 않아 certifi 번들로는 검증 실패 → OS 인증서 저장소 사용 (검증은 유지)
truststore.inject_into_ssl()

from common.paths import EGEN as OUT
from common.paths import EGEN_JSONL

URL = "https://www.e-gen.or.kr/egen/retrieve_pharmacy_list.do"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (academic research; seoul night pharmacy accessibility)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.e-gen.or.kr/egen/search_pharmacy.do",
}
# 서울 bbox + 여유. 반환 반경(~10km)보다 촘촘하게 7km 간격
LATS = np.arange(37.43, 37.72, 0.063)   # ≈ 7km
LONS = np.arange(126.76, 127.20, 0.079)  # ≈ 7km


def main() -> None:
    ap = argparse.ArgumentParser(description="E-Gen 약국 찾기 수집 (서울 격자 30점, 이미 받은 응답은 재사용)")
    ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.e-gen.or.kr/egen/search_pharmacy.do", timeout=30)  # 세션 쿠키
    merged: dict[str, dict] = {}
    centers = [(round(la, 4), round(lo, 4)) for la in LATS for lo in LONS]
    for k, (lat, lon) in enumerate(centers, 1):
        f = OUT / f"raw_{lat}_{lon}.json"
        if not f.exists():
            r = s.post(URL, data={"lat": lat, "lon": lon, "emogdesc": "", "day": "", "silson24": "N",
                                  "holidayY": "", "radius": "", "order": "", "currentPageNum": 1}, timeout=60)
            r.raise_for_status()
            f.write_text(r.text, encoding="utf-8")
            time.sleep(1.0)
        rows = json.loads(f.read_text(encoding="utf-8"))["list"]
        new = 0
        for row in rows:
            row.pop("DISTANCE", None), row.pop("DISTANCE2", None), row.pop("RN", None)
            new += row["EMOGCODE"] not in merged
            merged.setdefault(row["EMOGCODE"], row)
        print(f"[{k}/{len(centers)}] {lat},{lon}: {len(rows)}건, 신규 {new}, 누적 {len(merged)}")
    with open(EGEN_JSONL, "w", encoding="utf-8") as fh:
        for row in merged.values():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
