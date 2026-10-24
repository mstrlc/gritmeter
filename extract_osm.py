#!/usr/bin/env python3
"""Fill the data/osm/ tile cache from local Geofabrik .osm.pbf extracts (instead of Overpass).

Run with the venv that has pyosmium:  .venv/bin/python extract_osm.py data/pbf/*.osm.pbf
"""
import json
import math
import sys
from collections import defaultdict

import osmium

import surface_mix as sm


def main(paths):
    rides = sm.load_rides()
    needed = set().union(*(sm.tiles_for(r["points"]) for r in rides))
    print(f"{len(needed)} tiles needed")
    tiles = defaultdict(dict)  # tile -> {way_id: way}

    for path in paths:
        n = 0
        fp = (osmium.FileProcessor(path)
              .with_locations()
              .with_filter(osmium.filter.KeyFilter("highway")))
        for obj in fp:
            if not obj.is_way():
                continue
            try:
                geom = [(nd.lat, nd.lon) for nd in obj.nodes]
            except osmium.InvalidLocationError:
                continue
            hit = set()
            for (alat, alon), (blat, blon) in zip(geom, geom[1:]):
                for ty in range(math.floor(min(alat, blat) / sm.TILE_LAT), math.floor(max(alat, blat) / sm.TILE_LAT) + 1):
                    for tx in range(math.floor(min(alon, blon) / sm.TILE_LON), math.floor(max(alon, blon) / sm.TILE_LON) + 1):
                        if (ty, tx) in needed:
                            hit.add((ty, tx))
            if not hit:
                continue
            way = {"id": obj.id, "tags": {k: v for k, v in obj.tags if k in sm.KEEP_TAGS}, "geom": geom}
            for t in hit:
                tiles[t][obj.id] = way
            n += 1
        print(f"{path}: {n} relevant ways")

    sm.OSM_DIR.mkdir(parents=True, exist_ok=True)
    for t in needed:  # write every needed tile, even empty ones, so nothing falls back to Overpass
        (sm.OSM_DIR / f"{t[0]}_{t[1]}.json").write_text(json.dumps(list(tiles[t].values())))
    print(f"wrote {len(needed)} tiles, {sum(1 for t in needed if tiles[t])} non-empty")


if __name__ == "__main__":
    main(sys.argv[1:])
