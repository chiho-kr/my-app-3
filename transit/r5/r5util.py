"""r5py 네트워크 로딩 헬퍼.

주의 (r5py 1.1.7, Windows): r5py는 입력 파일을 CACHE_DIR/<파일이름>으로 복사(심링크 불가 시)하고,
같은 이름이 이미 있으면 **내용이 바뀌어도 다시 복사하지 않는다** (util/working_copy.py).
캐시 키(digest)도 그 복사본으로 계산하므로, 입력을 다시 만들어도 옛 네트워크가 로드된다.
→ load_network()는 캐시 복사본이 원본과 다르면 지우고 r5py에 넘긴다.
"""

import glob
import hashlib
import os
import sys

# r5py는 JVM 메모리를 명령행 인자에서 읽는다
if "--max-memory" not in sys.argv:
    sys.argv += ["--max-memory", "14G"]

# 사용자 환경변수를 바꾸지 않고 여기서만 JAVA_HOME 지정 (Temurin 21 휴대용 설치: %LOCALAPPDATA%\Programs\jdk-21*)
if "JAVA_HOME" not in os.environ:
    _jdk = sorted(glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "jdk-21*")))
    if _jdk:
        os.environ["JAVA_HOME"] = _jdk[-1]

import r5py  # noqa: E402
from r5py.util import Config  # noqa: E402


def _digest(p) -> str:
    with open(p, "rb") as f:
        return hashlib.file_digest(f, "blake2s").hexdigest()


def _refresh_working_copy(src) -> None:
    dst = Config().CACHE_DIR / src.name
    if dst.exists() and (dst.stat().st_size != src.stat().st_size or _digest(dst) != _digest(src)):
        dst.unlink()
        print(f"(r5py 캐시의 옛 복사본 교체: {src.name})")


def load_network(osm, gtfs: list) -> "r5py.TransportNetwork":
    for p in [osm, *gtfs]:
        _refresh_working_copy(p)
    return r5py.TransportNetwork(osm, gtfs)
