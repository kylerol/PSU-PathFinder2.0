"""
prepare_map.py
--------------
Builds the two map images the website uses, from the untouched uploads in
source_images/, and audits the JSON registries against the annotated map.

    python prepare_map.py            # (re)build static/images/*.png
    python prepare_map.py --audit    # re-detect red/blue/orange marks and compare with the JSON files
    python prepare_map.py --check    # validate walkway geometry against building footprints (no extra packages)

Building the images / auditing needs:  pip install -r requirements-tools.txt
The original uploads are never modified.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import map_processor as mp

BASE = Path(__file__).resolve().parent
SRC_ORIGINAL = BASE / "source_images" / "campus_map_original.png"
SRC_ANNOTATED = BASE / "source_images" / "campus_map_annotated.png"
SRC_REVISED = BASE / "source_images" / "campus_map_revised_annotated.png"   # image(2), before the Quadrangle path
SRC_LATEST = BASE / "source_images" / "campus_map_latest_annotated.png"     # image(3), authoritative
OUT_MAP = BASE / "static" / "images" / "campus_map.png"
OUT_ANNOTATED = BASE / "static" / "images" / "annotated_campus_map.png"
OUT_LATEST = BASE / "static" / "images" / "latest_annotated_campus_map.png"


def load(name):
    with open(BASE / name, encoding="utf-8") as fh:
        return json.load(fh)


def build_images():
    OUT_MAP.parent.mkdir(parents=True, exist_ok=True)
    mp.crop_original(SRC_ORIGINAL, OUT_MAP)
    mp.crop_and_align_annotated(SRC_ANNOTATED, OUT_ANNOTATED, mp.ANNOTATED_TO_ORIGINAL)
    mp.crop_and_align_annotated(SRC_LATEST, OUT_LATEST, mp.LATEST_ANNOTATED_TO_ORIGINAL)
    print(f"wrote {OUT_MAP.relative_to(BASE)}  ({mp.MAP_WIDTH_PX} x {mp.MAP_HEIGHT_PX})")
    print(f"wrote {OUT_ANNOTATED.relative_to(BASE)}  (earlier annotated map, black bars removed, aligned)")
    print(f"wrote {OUT_LATEST.relative_to(BASE)}  (latest annotated map, black bars removed, aligned)")


def px_dist(a, b):
    return math.dist(mp.pct_to_px(a), mp.pct_to_px(b))


def audit():
    if not OUT_LATEST.exists():
        build_images()
    marks = mp.detect_annotation_marks(OUT_LATEST)          # the latest map is authoritative
    entrances = load("entrances.json")["entrances"]
    locations = {l["building_id"]: l for l in load("locations.json")["locations"]}
    walk = load("walkways.json")
    ok = True

    # red dot -> main gate
    reds = marks["red"]
    gate = locations["main_gate"]
    print(f"Red marks found: {len(reds)}")
    if len(reds) != 1:
        ok = False
        print("  ! expected exactly one red dot")
    else:
        d = px_dist((reds[0][1], reds[0][2]), (gate["x_percent"], gate["y_percent"]))
        print(f"  main_gate is {d:.1f} px from the red dot")
        ok &= d <= 3

    # blue dots -> entrances
    blues = marks["blue"]
    revised_blues = []
    if SRC_REVISED.exists():
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "revised.png"
            mp.crop_and_align_annotated(SRC_REVISED, out, mp.LATEST_ANNOTATED_TO_ORIGINAL)
            revised_blues = mp.detect_annotation_marks(out)["blue"]
    print(f"Blue marks found: {len(blues)}   entrances registered: {len(entrances)}")
    unmatched = []
    for _, x, y in blues:
        best = min(entrances, key=lambda e: px_dist((x, y), (e["x_percent"], e["y_percent"])))
        d = px_dist((x, y), (best["x_percent"], best["y_percent"]))
        note = ""
        if d > 3 and revised_blues:
            # an orange stroke drawn over a blue dot on the latest map shifts its detected centre;
            # accept it when the same dot is clean on the revised map (image(2))
            d2 = min(px_dist((rx, ry), (best["x_percent"], best["y_percent"])) for _, rx, ry in revised_blues)
            if d2 <= 3:
                note = f"  (partly covered by an orange stroke; {d2:.1f} px on the revised map)"
                d = d2
        print(f"  blue ({x:6.2f}, {y:6.2f}) -> {best['entrance_id']:<22} {d:4.1f} px{note}")
        if d > 3:
            unmatched.append((x, y))
    ok &= not unmatched and len(blues) == len(entrances)

    import numpy as np

    def coverage(mask, segments):
        ys, xs = np.nonzero(mask)
        polylines = [[mp.pct_to_px(p) for p in sg["geometry_coordinates"]] for sg in segments]
        step = max(1, len(xs) // 4000)
        sample = list(zip(xs[::step], ys[::step]))
        if not sample or not polylines:
            return 0.0
        hit = sum(min(mp.point_segment_distance((x, y), a, b) for pl in polylines for a, b in zip(pl, pl[1:])) <= 8
                  for x, y in sample)
        return hit / len(sample)

    purple_segs = [sg for sg in walk["segments"] if sg.get("annotation_colour") == "purple"]
    orange_segs = [sg for sg in walk["segments"] if sg.get("annotation_colour") == "orange"]
    wrong = [sg["segment_id"] for sg in purple_segs if sg["segment_type"] != "original_walkway"]
    wrong += [sg["segment_id"] for sg in orange_segs if sg["segment_type"] != "alternative_walkway"]
    cp = coverage(marks["purple_mask"], purple_segs)
    print(f"Purple pixels within 8 px of a modelled ORIGINAL walkway: {cp:.1%}  ({len(marks['purple'])} strokes)")
    # orange that is NOT hidden under purple must be covered by an alternative walkway
    orange_only = marks["orange_mask"] & ~_dilate(marks["purple_mask"], 6)
    co = coverage(orange_only, orange_segs)
    print(f"Orange pixels (not under purple) within 8 px of a modelled ALTERNATIVE walkway: {co:.1%}")
    if wrong:
        print("  ! wrong classification:", wrong)
    ok &= cp >= 0.95 and co >= 0.90 and not wrong

    print(f"Green marks (recorded, not used): {len(marks['green'])}")
    print("AUDIT PASSED" if ok else "AUDIT FOUND DIFFERENCES")
    return ok


def _dilate(mask, r):
    from scipy import ndimage
    import numpy as np
    return ndimage.binary_dilation(mask, structure=np.ones((2 * r + 1, 2 * r + 1)))


def check_geometry():
    walk = load("walkways.json")
    rep = mp.validate_walkway_registry(walk["nodes"], walk["segments"], load("footprints.json")["footprints"],
                                       load("entrances.json")["entrances"])
    print(f"{rep['nodes']} nodes, {rep['segments']} segments, {rep['connected_nodes']} connected to the Main Gate")
    for e in rep["errors"]:
        print("  ERROR", e)
    for w in rep["warnings"]:
        print("  warning", w)
    print("GEOMETRY OK - no walkway passes through a building" if rep["ok"] else "GEOMETRY ERRORS")
    return rep["ok"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        sys.exit(0 if check_geometry() else 1)
    if args.audit:
        sys.exit(0 if audit() else 1)
    build_images()
