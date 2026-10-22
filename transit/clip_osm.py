"""Geofabrik 한국 OSM → 분석 영역(config.BBOX) PBF. osmium-tool 없이 pyosmium으로 처리.

- 영역 안 노드는 모두 남긴다 (약국·정류장 같은 단독 POI 포함).
- 노드가 하나라도 영역 안에 있는 way는 통째로 남기고, 그 way가 쓰는 노드는 영역 밖이어도 모두 넣는다
  (경계에서 도로가 끊기지 않게: osmium extract --strategy complete_ways 와 같은 방식).
- relation은 남긴 노드·way를 멤버로 가진 것만 남긴다 (보행에는 회전 제한 정도만 쓰임).

  python -m transit.clip_osm
"""

import argparse

import osmium

from common.paths import BBOX, OSM_CLIP, RAW_OSM
from common.paths import TRANSIT as OUT


def inside(lat, lon) -> bool:
    return BBOX["lat_min"] <= lat <= BBOX["lat_max"] and BBOX["lon_min"] <= lon <= BBOX["lon_max"]


def main() -> None:
    argparse.ArgumentParser(description="Geofabrik 한국 OSM → 분석 영역 PBF").parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) 영역 안 노드
    in_nodes = set()
    for n in osmium.FileProcessor(str(RAW_OSM), osmium.osm.NODE):
        if n.location.valid() and inside(n.location.lat, n.location.lon):
            in_nodes.add(n.id)
    print("영역 안 노드", len(in_nodes))

    # 2) 노드가 하나라도 영역 안인 way와, 그 way의 전체 노드
    keep_ways, need_nodes = set(), set()
    for w in osmium.FileProcessor(str(RAW_OSM), osmium.osm.WAY):
        refs = [r.ref for r in w.nodes]
        if any(r in in_nodes for r in refs):
            keep_ways.add(w.id)
            need_nodes.update(refs)
    print("way", len(keep_ways), "필요 노드", len(need_nodes))

    # 3) 쓰기
    if OSM_CLIP.exists():
        OSM_CLIP.unlink()
    writer = osmium.SimpleWriter(str(OSM_CLIP))
    n_rel = 0
    try:
        for o in osmium.FileProcessor(str(RAW_OSM)):
            if o.is_node():
                # way 구성 노드 + 영역 안 단독 노드(약국·정류장 같은 POI)
                if o.id in need_nodes or o.id in in_nodes:
                    writer.add_node(o)
            elif o.is_way():
                if o.id in keep_ways:
                    writer.add_way(o)
            elif o.is_relation():
                if any((m.type == "n" and (m.ref in need_nodes or m.ref in in_nodes)) or (m.type == "w" and m.ref in keep_ways) for m in o.members):
                    writer.add_relation(o)
                    n_rel += 1
    finally:
        writer.close()
    print("relation", n_rel, "→", OSM_CLIP, f"{OSM_CLIP.stat().st_size / 2**20:.0f} MB")


if __name__ == "__main__":
    main()
