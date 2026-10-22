"""[교차검증 출처] 휴일지킴이약국(pharm114.or.kr, 대한약사회) 약국 목록·상세 수집기.

Source: 사이트 SPA가 호출하는 JSON API (2026-10 확인).
  POST /api/common/v1/getGugunList  {"sido": "서울"}            -> 구/군 목록
  POST /api/pharm/v1/getPharmList   {"sido","gugun","pageNum"}  -> 100건/page 목록
  POST /api/pharm/v1/getPharmInfo   {"pharmacyCode"}            -> 요일별 영업시간 등 상세

주의: gugun을 비우면 sido 필터가 무시되어 전국 목록이 반환된다. 그래서 반드시 구/군 단위로 순회한다.

산출물 (data/pharm114/):
  raw/list_{sido}.jsonl         목록 API 원본 (구/군 정보 부착)
  raw/info/{pharmacyCode}.json  상세 API 원본 (캐시 겸용 → 재실행 시 이어받기)

사용 예:
  python -m pharmacy.pharm114_collect                          # 서울 전체
  python -m pharmacy.pharm114_collect --sido 서울 경기 인천     # 경계효과용 인접 시도 포함
  python -m pharmacy.pharm114_collect --gugun 종로구 --limit 50 # 테스트
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

from common.paths import PHARM114

BASE = "https://pharm114.or.kr"
HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.pharm114.or.kr",
    "Referer": "https://www.pharm114.or.kr/searchMapView",
    "User-Agent": "Mozilla/5.0 (academic research; seoul night pharmacy accessibility)",
}


class Client:
    def __init__(self, delay: float):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.delay = delay

    def post(self, path: str, body: dict, retries: int = 4) -> dict:
        for attempt in range(retries):
            time.sleep(self.delay * (0.75 + random.random() / 2))
            try:
                r = self.s.post(BASE + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=30)
                r.raise_for_status()
                d = r.json()
                if d.get("code") != 200:
                    raise RuntimeError(f"api code {d.get('code')}: {d.get('message')}")
                return d
            except Exception as e:  # noqa: BLE001 - 네트워크/서버 오류는 재시도
                wait = 2 ** attempt * 2
                print(f"  ! {path} {body} 실패({e}); {wait}s 후 재시도", file=sys.stderr)
                time.sleep(wait)
        raise RuntimeError(f"{path} {body} 재시도 초과")


def collect_list(c: Client, sido: str, only_gugun: list[str] | None) -> list[dict]:
    guguns = [g["value"] for g in c.post("/api/common/v1/getGugunList", {"sido": sido})["commonCodeList"]]
    if only_gugun:
        guguns = [g for g in guguns if g in only_gugun]
    rows: dict[str, dict] = {}
    for gugun in guguns:
        page, total_page, n = 1, 1, 0
        while page <= total_page:
            d = c.post(
                "/api/pharm/v1/getPharmList",
                {"pageNum": page, "needTotalCount": page == 1, "keyword": "", "sido": sido, "gugun": gugun},
            )
            # totalCount/totalPage는 needTotalCount=true인 1페이지에서만 채워진다 (이후 페이지는 0)
            if page == 1:
                expected = d["paginate"]["totalCount"]
                total_page = d["paginate"]["totalPage"]
            for p in d["pharmList"]:
                p["_sido"], p["_gugun"] = sido, gugun
                rows.setdefault(p["pharmacyCode"], p)
                n += 1
            page += 1
        flag = "" if n == expected else f"  (!! totalCount={expected})"
        print(f"  {sido} {gugun}: {n}건{flag}")
    return list(rows.values())


def collect_info(c: Client, codes: list[str], info_dir: Path) -> None:
    info_dir.mkdir(parents=True, exist_ok=True)
    todo = [k for k in codes if not (info_dir / f"{k}.json").exists()]
    print(f"상세: 전체 {len(codes)} / 캐시 {len(codes) - len(todo)} / 신규 {len(todo)}")
    for i, code in enumerate(todo, 1):
        d = c.post("/api/pharm/v1/getPharmInfo", {"pharmacyCode": code})
        d["_fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        (info_dir / f"{code}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        if i % 200 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sido", nargs="+", default=["서울"], help="사이트의 sido 값 (서울, 경기, 인천 …)")
    ap.add_argument("--gugun", nargs="*", help="특정 구/군만 (테스트용)")
    ap.add_argument("--limit", type=int, help="상세 수집 최대 건수 (테스트용)")
    ap.add_argument("--delay", type=float, default=0.3, help="요청 간 평균 대기(초)")
    ap.add_argument("--out", type=Path, default=PHARM114)
    a = ap.parse_args()

    c = Client(a.delay)
    raw = a.out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    codes: list[str] = []
    for sido in a.sido:
        print(f"[목록] {sido}")
        rows = collect_list(c, sido, a.gugun)
        with open(raw / f"list_{sido}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        codes += [r["pharmacyCode"] for r in rows]
    codes = list(dict.fromkeys(codes))[: a.limit]
    collect_info(c, codes, raw / "info")


if __name__ == "__main__":
    main()
