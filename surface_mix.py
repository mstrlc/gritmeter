#!/usr/bin/env python3
"""Surface mix (asphalt / gravel / rocky) of Garmin gravel rides, via OpenStreetMap.

Pipeline per ride:
  GPX track -> resample every SAMPLE_M metres -> fetch nearby OSM ways (Overpass, cached)
  -> snap each sample to the nearest way within SNAP_M -> classify(way tags) -> sum distance.

Usage:
  python3 surface_mix.py            # fetch (cached) + analyse all rides in data/gpx
  python3 surface_mix.py --fetch    # only download/cache OSM data
"""
import json
import math
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
GPX_DIR = ROOT / "data" / "gpx"
OSM_DIR = ROOT / "data" / "osm"
OUT_JSON = ROOT / "data" / "results.json"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SAMPLE_M = 20         # distance between analysed track samples
SNAP_M = 20           # max distance from a sample to a way to count as "on" it
SMOOTH_WINDOW = 5     # majority filter over N samples (~100 m) to kill intersection flicker
TILE_LAT, TILE_LON = 0.02, 0.03   # Overpass tile size (~2.2 x 2.2 km in CZ)
KEEP_TAGS = {"highway", "surface", "tracktype", "smoothness", "mtb:scale", "name", "footway", "bicycle"}


def snap_penalty(tags):
    """Extra metres added to the distance to a way when snapping.

    Sidewalks are mapped as separate footways a few metres beside the road; without a penalty, GPS drift
    of 3-5 m snaps road riding onto the (often paving_stones) sidewalk. Real footpaths with no road
    nearby still win because nothing else is in range.
    """
    hw = tags.get("highway")
    if hw in ("footway", "pedestrian", "steps") and tags.get("bicycle") not in ("yes", "designated"):
        return 15 if tags.get("footway") in ("sidewalk", "crossing") else 8
    if hw == "cycleway" or (hw == "footway" and tags.get("bicycle") in ("yes", "designated")):
        return 3  # cycle tracks beside roads: you might be on either
    return 0

CATEGORIES = ("asphalt", "gravel", "rocky", "unknown")

# "asphalt" = any sealed surface (asphalt, concrete, paving stones, sett)
PAVED = {"asphalt", "paved", "concrete", "paving_stones", "sett", "cobblestone", "unhewn_cobblestone",
         "concrete:plates", "concrete:lanes", "metal", "wood", "chipseal"}
LOOSE = {"gravel", "fine_gravel", "compacted", "unpaved", "dirt", "ground", "earth", "grass", "mud",
         "sand", "grass_paver", "woodchips", "clay"}
ROCKY_SURFACES = {"rock", "stone", "pebblestone", "rocks", "scree"}
ROUGH_SMOOTHNESS = {"bad", "very_bad", "horrible", "very_horrible", "impassable"}
PAVED_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
                  "living_street", "service", "primary_link", "secondary_link", "tertiary_link", "pedestrian"}


# ---------------------------------------------------------------- classification

def classify(tags: dict) -> str:
    """Map an OSM way's tags to one of CATEGORIES.

    Useful tags: surface, highway, tracktype (grade1..grade5), smoothness, mtb:scale.
    """
    surface = tags.get("surface", "")
    highway = tags.get("highway", "")
    grade = tags.get("tracktype", "")
    smooth = tags.get("smoothness", "")
    mtb = tags.get("mtb:scale", "0")[:1]

    # rough-going signals win over a nominal surface (a "gravel" track can still be a rock garden)
    if (surface in ROCKY_SURFACES or smooth in ROUGH_SMOOTHNESS
            or (mtb.isdigit() and int(mtb) >= 2) or grade in ("grade4", "grade5")) and surface not in PAVED:
        return "rocky"
    if surface in PAVED or surface.startswith("concrete"):
        return "asphalt"
    if surface in LOOSE:
        return "gravel"
    if surface:
        return "unknown"
    # untagged surface: infer from road class / track grade
    if grade == "grade1" or highway in PAVED_HIGHWAYS:
        return "asphalt"
    if grade in ("grade2", "grade3") or highway in ("track", "bridleway"):
        return "gravel"
    return "unknown"  # untagged path/footway/cycleway: could be anything


# ---------------------------------------------------------------- geometry helpers

def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


class LocalProj:
    """Equirectangular projection around a reference latitude — accurate to <0.1% over a ride."""

    def __init__(self, lat0):
        self.kx = 111320 * math.cos(math.radians(lat0))
        self.ky = 110540

    def __call__(self, lat, lon):
        return lon * self.kx, lat * self.ky


def point_seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def resample(points, step):
    """Return points spaced ~step metres apart along the track."""
    out = [points[0]]
    acc = 0.0
    for prev, cur in zip(points, points[1:]):
        acc += haversine(prev, cur)
        if acc >= step:
            out.append(cur)
            acc = 0.0
    return out


# ---------------------------------------------------------------- IO

def read_gpx(path):
    pts = []
    for _, el in ET.iterparse(path):
        if el.tag.endswith("trkpt"):
            pts.append((float(el.get("lat")), float(el.get("lon"))))
            el.clear()
    return pts


def decode_polyline(s):
    """Google encoded polyline -> [(lat, lon)]."""
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        pts.append((lat / 1e5, lon / 1e5))
    return pts


def densify(points, step):
    """Insert points along straight segments so sparse polylines get sampled every ~step m."""
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, int(haversine(a, b) // step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    return out


def load_rides():
    """Garmin rides (full GPX) + older Strava-only rides (reduced polyline, densified)."""
    rides = []
    for r in json.loads((ROOT / "data" / "rides.json").read_text()):
        rides.append({**r, "source": "garmin", "points": read_gpx(GPX_DIR / f"{r['id']}.gpx")})
    for r in json.loads((ROOT / "data" / "strava_polylines.json").read_text()):
        pts = densify(decode_polyline(r.pop("polyline")), 5)
        rides.append({**r, "source": "strava", "points": pts})
    return rides


def tiles_for(points):
    """Grid tiles touched by the track, padded so points near a tile edge still see neighbours."""
    pad_lat, pad_lon = 0.0004, 0.0006  # ~45 m
    tiles = set()
    for lat, lon in resample(points, 50):
        for dlat in (-pad_lat, pad_lat):
            for dlon in (-pad_lon, pad_lon):
                tiles.add((math.floor((lat + dlat) / TILE_LAT), math.floor((lon + dlon) / TILE_LON)))
    return tiles


def fetch_tile(ty, tx):
    """All highway ways in one grid tile, cached in data/osm/<ty>_<tx>.json (shared across rides)."""
    cache = OSM_DIR / f"{ty}_{tx}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    s, w = ty * TILE_LAT, tx * TILE_LON
    q = f"[out:json][timeout:90];way[highway]({s:.4f},{w:.4f},{s + TILE_LAT:.4f},{w + TILE_LON:.4f});out tags geom;"
    for attempt in range(20):
        try:
            req = urllib.request.Request(
                OVERPASS_URL,
                data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": "pathfinder-surface-mix/1.0"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            break
        except Exception as e:  # the public instance 504s ~50% of the time under load
            time.sleep(min(5 + 3 * attempt, 30))
    else:
        raise RuntimeError(f"Overpass failed for tile {ty}_{tx}")
    ways = [
        {
            "id": el["id"],
            "tags": {k: v for k, v in el.get("tags", {}).items() if k in KEEP_TAGS},
            "geom": [(g["lat"], g["lon"]) for g in el["geometry"]],
        }
        for el in data.get("elements", [])
    ]
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(ways))
    return ways


def prefetch(all_tiles, workers=2):
    """Download every missing tile with a small thread pool (Overpass allows 2 slots)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    todo = [t for t in sorted(all_tiles) if not (OSM_DIR / f"{t[0]}_{t[1]}.json").exists()]
    print(f"{len(all_tiles)} tiles, {len(todo)} to download", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_tile, *t): t for t in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                f.result()
            except Exception as e:
                print(f"  FAILED {futs[f]}: {e}", flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}", flush=True)


def fetch_osm(points):
    ways = {}
    for t in sorted(tiles_for(points)):
        for w in fetch_tile(*t):
            ways[w["id"]] = w
    return list(ways.values())


# ---------------------------------------------------------------- matching

def match(points, ways, classifier=None):
    """Snap the track to OSM ways. Returns (samples, smoothed labels, matched way index or None per sample)."""
    samples = resample(points, SAMPLE_M)
    proj = LocalProj(samples[0][0])
    cell = 50.0

    # grid index of way segments
    grid = defaultdict(list)
    for wi, w in enumerate(ways):
        g = [proj(*p) for p in w["geom"]]
        for a, b in zip(g, g[1:]):
            for cx in range(int(min(a[0], b[0]) // cell) - 1, int(max(a[0], b[0]) // cell) + 2):
                for cy in range(int(min(a[1], b[1]) // cell) - 1, int(max(a[1], b[1]) // cell) + 2):
                    grid[(cx, cy)].append((a, b, wi))

    cats_by_way = [(classifier or classify)(w["tags"]) for w in ways]
    penalty = [snap_penalty(w["tags"]) for w in ways]

    labels, matched = [], []
    for lat, lon in samples:
        p = proj(lat, lon)
        best, best_d = None, float("inf")
        for a, b, wi in grid.get((int(p[0] // cell), int(p[1] // cell)), ()):
            d = point_seg_dist(p, a, b)
            if d <= SNAP_M and d + penalty[wi] < best_d:
                best, best_d = wi, d + penalty[wi]
        labels.append("unknown" if best is None else cats_by_way[best])
        matched.append(best)

    # majority smoothing: a single sample snapping to a crossing road shouldn't count
    half = SMOOTH_WINDOW // 2
    smoothed = [Counter(labels[max(0, i - half):i + half + 1]).most_common(1)[0][0] for i in range(len(labels))]
    return samples, smoothed, matched


def analyse(points, ways, classifier=None):
    samples, smoothed, _ = match(points, ways, classifier)
    dist = Counter()
    for (a, b), lab in zip(zip(samples, samples[1:]), smoothed):
        dist[lab] += haversine(a, b)
    return dist


# ---------------------------------------------------------------- main

def main():
    rides = load_rides()
    all_tiles = set().union(*(tiles_for(r["points"]) for r in rides))
    prefetch(all_tiles)
    if "--fetch" in sys.argv:
        return

    results, totals = [], Counter()
    for ride in rides:
        ways = fetch_osm(ride["points"])
        d = analyse(ride["points"], ways)
        total = sum(d.values())
        pct = {c: 100 * d[c] / total for c in CATEGORIES}
        print(f"{ride['date']}  {ride['source']:6s} {ride['name'][:34]:34s}"
              + "  ".join(f"{c[:4]} {pct[c]:5.1f}%" for c in CATEGORIES) + f"   {total / 1000:6.1f} km")
        if str(ride["id"]).startswith("validate_"):
            continue  # duplicate of a Garmin ride, used only to check polyline accuracy
        totals.update(d)
        results.append({k: v for k, v in ride.items() if k != "points"} | {"km": {c: round(d[c] / 1000, 2) for c in CATEGORIES}})

    grand = sum(totals.values())
    print(f"\nALL {len(results)} GRAVEL RIDES  "
          + "  ".join(f"{c}: {totals[c] / 1000:.0f} km ({100 * totals[c] / grand:.1f}%)" for c in CATEGORIES))
    OUT_JSON.write_text(json.dumps({"rides": results, "totals_km": {c: round(totals[c] / 1000, 1) for c in CATEGORIES}}, indent=1))
    print(f"saved {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
