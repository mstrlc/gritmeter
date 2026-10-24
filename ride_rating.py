#!/usr/bin/env python3
"""Finer surface breakdown (6 levels) + a 0-100 roughness score per ride.

Reuses the matching pipeline from surface_mix.py; only the classifier differs.
"""
import json
import re
from collections import Counter

import surface_mix as sm

# level -> roughness weight (0 = smooth asphalt, 5 = rocky/rough)
LEVELS = {
    "asphalt": 0,      # asphalt, chipseal; untagged roads
    "paved_rough": 1,  # paving stones, sett, cobbles, concrete plates
    "hardpack": 2,     # compacted, fine gravel; grade2 tracks
    "gravel": 3,       # loose gravel, generic unpaved; grade3 tracks
    "dirt": 4,         # dirt, ground, grass, mud, sand
    "rough": 5,        # rock, bad smoothness, grade4-5, mtb:scale >= 2
}
ORDER = list(LEVELS) + ["unknown"]

SMOOTH_PAVED = {"asphalt", "chipseal", "paved"}  # bare "paved" is almost always asphalt in CZ
ROUGH_PAVED = {"paving_stones", "sett", "cobblestone", "unhewn_cobblestone", "concrete",
               "concrete:plates", "concrete:lanes", "metal", "wood", "grass_paver"}
HARDPACK = {"compacted", "fine_gravel"}
GRAVEL = {"gravel", "unpaved", "pebblestone"}
DIRT = {"dirt", "ground", "earth", "grass", "mud", "sand", "clay", "woodchips"}


def classify_fine(tags):
    surface = tags.get("surface", "")
    grade = tags.get("tracktype", "")
    smooth = tags.get("smoothness", "")
    mtb = tags.get("mtb:scale", "0")[:1]
    paved = surface in SMOOTH_PAVED or surface in ROUGH_PAVED

    if not paved and (surface in sm.ROCKY_SURFACES or smooth in sm.ROUGH_SMOOTHNESS
                      or (mtb.isdigit() and int(mtb) >= 2) or grade in ("grade4", "grade5")):
        return "rough"
    if surface in SMOOTH_PAVED:
        return "asphalt"
    if surface in ROUGH_PAVED:
        return "paved_rough"
    if surface in HARDPACK:
        return "hardpack"
    if surface in GRAVEL:
        return "gravel"
    if surface in DIRT:
        return "dirt"
    if surface:
        return "unknown"
    # untagged: infer from road class / track grade
    if grade == "grade1" or tags.get("highway") in sm.PAVED_HIGHWAYS:
        return "asphalt"
    if grade == "grade2":
        return "hardpack"
    if grade == "grade3" or tags.get("highway") in ("track", "bridleway"):
        return "gravel"
    return "unknown"


def score(d):
    """Distance-weighted mean roughness, scaled to 0-100 (unknown excluded)."""
    known = sum(d[k] for k in LEVELS)
    return 0 if not known else 20 * sum(d[k] * w for k, w in LEVELS.items()) / known


def label(s):
    return ("road" if s < 5 else "mostly road" if s < 10 else "mixed" if s < 20
            else "gravel" if s < 30 else "rough gravel")


def rate(points):
    """Rate one ride from its [(lat, lon)] track: {km, score, label, pct per level in ORDER}."""
    d = sm.analyse(points, sm.fetch_osm(points), classify_fine)
    t = sum(d.values()) or 1
    s = score(d)
    return {"km": round(t / 1000, 1), "score": round(s, 1), "label": label(s),
            "pct": {k: round(100 * d[k] / t, 1) for k in ORDER}}


# ---------------------------------------------------------------- Strava description block (format fixed in PLAN.md)

MARKER = "surface by gritmeter.app"
CREDIT = "map data © OpenStreetMap contributors"
MIN_PCT = 5  # shares below this are left out of the summary line
DESC_NAMES = {"paved_rough": "rough pavement"}


def description_block(r):
    """The 4-line block gritmeter appends to an activity description."""
    shares = sorted(((p, k) for k, p in r["pct"].items() if p >= MIN_PCT), reverse=True)
    mix = " · ".join(f"{int(p + 0.5)}% {DESC_NAMES.get(k, k)}" for p, k in shares)  # half up, not to even
    return f"{MARKER}\n{mix}\nroughness {int(r['score'] + 0.5)} / 100, {r['label']}\n{CREDIT}"


BLANK_LINE = re.compile(r"\r?\n[ \t]*\r?\n")


def _eol(text):
    """The line ending the description already uses (other apps often write \\r\\n), so ours matches."""
    return "\r\n" if "\r\n" in text else "\n"


def _block_span(text):
    """(start, end) of our block in text, or None. It runs from the marker to the end of its credit line; if the
    credit line was edited away, to the end of the marker's paragraph, so text after it is never swallowed."""
    start = text.find(MARKER)
    if start == -1:
        return None
    end = text.find(CREDIT, start)
    para = BLANK_LINE.search(text, start)
    if end != -1 and (para is None or para.start() > end):
        return start, end + len(CREDIT)
    return start, len(text) if para is None else para.start()


def strip_block(text):
    """The rider's text with our block taken out; the text before and after it stays one blank line apart."""
    text = text or ""
    span = _block_span(text)
    if not span:
        return text
    before, after = text[:span[0]].rstrip(), text[span[1]:].strip()
    return before + _eol(text) * 2 + after if before and after else before or after


def merge_description(text, block):
    """Our block in the rider's text: replaced where it already is, otherwise appended after one blank line.
    The block takes the description's line endings."""
    text = text or ""
    eol = _eol(text)
    block = block.replace("\n", eol)
    span = _block_span(text)
    if span:
        return text[:span[0]] + block + text[span[1]:]
    return text.rstrip() + eol * 2 + block if text.strip() else block


def main():
    rides = [r for r in sm.load_rides() if not str(r["id"]).startswith("validate_")]
    out, totals = [], Counter()
    for r in rides:
        d = sm.analyse(r["points"], sm.fetch_osm(r["points"]), classify_fine)
        totals.update(d)
        t = sum(d.values())
        s = score(d)
        out.append({"id": r["id"], "date": r["date"], "name": r["name"], "km": round(t / 1000, 1),
                    "score": round(s, 1), "label": label(s),
                    "pct": {k: round(100 * d[k] / t, 1) for k in ORDER}})

    hdr = "  ".join(f"{k[:6]:>6}" for k in ORDER)
    print(f"{'date':10}  {'ride':34} {'km':>6}  {'score':>5}  {'rating':12} {hdr}")
    for o in sorted(out, key=lambda o: -o["score"]):
        print(f"{o['date']}  {o['name'][:34]:34} {o['km']:6.1f}  {o['score']:5.1f}  {o['label']:12} "
              + "  ".join(f"{o['pct'][k]:6.1f}" for k in ORDER))
    t = sum(totals.values())
    print(f"\nALL {len(out)} rides, {t / 1000:.0f} km, score {score(totals):.1f}: "
          + "  ".join(f"{k} {100 * totals[k] / t:.1f}%" for k in ORDER))
    print("ratings:", dict(Counter(o["label"] for o in out)))
    (sm.ROOT / "data" / "ratings.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
