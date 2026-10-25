#!/usr/bin/env python3
"""Render one ride as an interactive Leaflet map, coloured by surface level.

Usage: python3 make_map.py [ride_id]   (defaults to the newest ride)
"""
import json
import sys
from collections import Counter

import ride_rating as rr
import surface_mix as sm

# Surface symbology from design/identity.html: colour warms and the line breaks up as the grade rises,
# so the map still reads in greyscale. Dash lengths are in px at line weight 5; "" = solid.
COLORS = {
    "asphalt": "#3a3f46",
    "paved_rough": "#7487a3",
    "hardpack": "#d3ab3c",
    "gravel": "#dc7429",
    "dirt": "#8a522c",
    "rough": "#c42e2a",
    "unknown": "#a7aca2",
}
DASH = {
    "asphalt": "", "paved_rough": "16 3", "hardpack": "12 6", "gravel": "7 5",
    "dirt": "3 5", "rough": "0.1 10", "unknown": "",
}
GRADES = {
    "asphalt": "G0", "paved_rough": "G1", "hardpack": "G2", "gravel": "G3",
    "dirt": "G4", "rough": "G5", "unknown": "G?",
}
NAMES = {
    "asphalt": "Asphalt", "paved_rough": "Paved, rough", "hardpack": "Hardpack",
    "gravel": "Gravel", "dirt": "Dirt", "rough": "Rough", "unknown": "Unknown",
}


def describe(tags):
    bits = [tags.get("name") or tags.get("highway", "?")]
    for k in ("surface", "tracktype", "smoothness", "mtb:scale"):
        if k in tags:
            bits.append(f"{k}={tags[k]}")
    return " · ".join(bits)


def build(ride):
    ways = sm.fetch_osm(ride["points"])
    samples, labels, matched = sm.match(ride["points"], ways, rr.classify_fine)

    segs, start, km = [], 0, 0.0
    for i in range(1, len(samples) + 1):
        if i == len(samples) or labels[i] != labels[start]:
            pts = samples[start:min(i + 1, len(samples))]  # overlap one point so lines join up
            length = sum(sm.haversine(a, b) for a, b in zip(pts, pts[1:]))
            way = Counter(m for m in matched[start:i] if m is not None).most_common(1)
            segs.append({
                "cat": labels[start],
                "km": round(km / 1000, 2),
                "len": round(length),
                "way": describe(ways[way[0][0]]["tags"]) if way else "no mapped way nearby",
                "pts": [[round(a, 5), round(b, 5)] for a, b in pts],
            })
            km += length
            start = i

    dist = Counter()
    for s in segs:
        dist[s["cat"]] += s["len"]
    total = sum(dist.values())
    return {
        "name": ride["name"], "date": ride["date"], "km": round(total / 1000, 1),
        "score": round(rr.score(dist), 1), "label": rr.label(rr.score(dist)),
        "pct": {k: round(100 * dist[k] / total, 1) for k in rr.ORDER},
        "segs": segs,
    }


def main():
    rides = [r for r in sm.load_rides() if not str(r["id"]).startswith("validate_")]
    ride = (next(r for r in rides if str(r["id"]) == sys.argv[1]) if len(sys.argv) > 1
            else max(rides, key=lambda r: r["date"]))
    data = build(ride)
    tpl = (sm.ROOT / "map_template.html").read_text()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__COLORS__", json.dumps(COLORS))
               .replace("__NAMES__", json.dumps(NAMES))
               .replace("__DASH__", json.dumps(DASH))
               .replace("__GRADES__", json.dumps(GRADES))
               .replace("__TITLE__", f"{ride['date']} surface map"))
    out = sm.ROOT / "maps" / f"{ride['id']}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    print(out)


if __name__ == "__main__":
    main()
