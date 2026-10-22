"""MOTIS(osr)용 OSM: 길(way) 17개 이상이 만나는 노드를 쪼갠다.

osr는 한 노드에 연결된 way를 최대 16개까지만 허용한다 (VERIFY FAIL: node … has 18 ways, maximum is 16).
서울 OSM에는 보도 18개가 한 점에서 만나는 광장 같은 노드가 있다.
→ 그런 노드에 붙은 way 중 14개를 넘는 것들을 같은 좌표의 복제 노드로 옮기고, 원 노드와 복제 노드를
  길이 0인 highway=footway로 잇는다 (보행 연결 유지, 차량 경로에는 영향이 미미).
r5py용 capital.osm.pbf는 그대로 두고, data/motis/capital_motis.osm.pbf를 따로 만든다.

  python -m transit.osm_for_motis
"""

import argparse
import collections

import osmium

from common.paths import MOTIS_OSM as OUT
from common.paths import OSM_CLIP

MAX_WAYS = 16
KEEP = 14          # 원 노드에 남길 way 수 (+ 연결 way 1~2개 ≤ 16)
NEW_ID = 90_000_000_000_000


def main() -> None:
    argparse.ArgumentParser(description="MOTIS용 OSM (way 17개 이상 만나는 노드 쪼개기)").parse_args()
    deg = collections.Counter()
    for w in osmium.FileProcessor(str(OSM_CLIP), osmium.osm.WAY):
        for n in {n.ref for n in w.nodes}:
            deg[n] += 1
    hubs = {n for n, d in deg.items() if d > MAX_WAYS}
    print(f"way {MAX_WAYS}개 초과 노드 {len(hubs)}개 (최대 {max(deg.values())})")

    # 각 허브 노드에서 몇 번째 way부터 옮길지 정하고, 옮길 way → 새 노드 id
    seen = collections.Counter()
    moved: dict[tuple[int, int], int] = {}       # (way id, 원 노드) → 복제 노드
    clones: dict[int, list[int]] = collections.defaultdict(list)
    for w in osmium.FileProcessor(str(OSM_CLIP), osmium.osm.WAY):
        for n in {n.ref for n in w.nodes} & hubs:
            seen[n] += 1
            if seen[n] > KEEP:
                k = (seen[n] - KEEP - 1) // KEEP           # 복제 노드 하나당 KEEP개까지
                while len(clones[n]) <= k:
                    clones[n].append(NEW_ID + len(moved) + sum(len(v) for v in clones.values()))
                moved[(w.id, n)] = clones[n][k]

    loc = {}
    for nd in osmium.FileProcessor(str(OSM_CLIP), osmium.osm.NODE):
        if nd.id in hubs:
            loc[nd.id] = nd.location

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    wr = osmium.SimpleWriter(str(OUT))
    next_way = NEW_ID
    clones_written = False
    try:
        # PBF는 node·way·relation 순, 각각 id 오름차순이어야 한다 → 복제 노드는 원본 노드 뒤에, 연결 way는 원본 way 뒤에
        for o in osmium.FileProcessor(str(OSM_CLIP)):
            if o.is_node():
                wr.add_node(o)
                continue
            if not clones_written:
                for c, n in sorted((c, n) for n, cs in clones.items() for c in cs):
                    wr.add_node(osmium.osm.mutable.Node(id=c, location=loc[n], version=1, tags={}))
                clones_written = True
            if o.is_way():
                if any((o.id, n.ref) in moved for n in o.nodes):
                    nodes = [moved.get((o.id, n.ref), n.ref) for n in o.nodes]
                    wr.add_way(o.replace(nodes=nodes))
                else:
                    wr.add_way(o)
            else:
                if next_way == NEW_ID:   # relation 앞에 연결 way를 한 번만 쓴다 (PBF 순서: node, way, relation)
                    for n, cs in clones.items():
                        for c in cs:
                            wr.add_way(osmium.osm.mutable.Way(id=next_way, nodes=[n, c], version=1,
                                                              tags={"highway": "footway", "note": "motis node split"}))
                            next_way += 1
                wr.add_relation(o)
    finally:
        wr.close()
    print(f"복제 노드 {sum(len(v) for v in clones.values())}개, 옮긴 way 연결 {len(moved)}개 → {OUT}")


if __name__ == "__main__":
    main()
