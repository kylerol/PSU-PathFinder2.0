"""
map_processor.py
----------------
Everything that has to do with the campus MAP and its GEOMETRY.

Part 1 (pure Python, used at runtime by graph.py and app.py)
    * coordinate helpers  (percent  <->  map pixels)
    * polyline length
    * point-in-polygon (ray casting) and point-to-segment distance
    * building-collision check for a route polyline
    * validation of the whole walkway registry before it goes into SQLite

Part 2 (image tools, used only by prepare_map.py; needs Pillow + numpy)
    * crop the original map out of its legend panel
    * crop the black phone bars off the annotated map and align it
    * re-detect the red / blue / orange marks for the audit

Coordinate system (shared by EVERY layer of the website):
    x = percent of the map width  from the LEFT edge  (0 - 100)
    y = percent of the map height from the TOP edge   (0 - 100)
    measured on the cropped original map (944 x 1472 px).
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# Reference frame (the cropped original map that the website shows)
MAP_WIDTH_PX = 944
MAP_HEIGHT_PX = 1472

# Crop boxes (left, top, right, bottom) in the uploaded images
ORIGINAL_CROP_BOX = (13, 1, 957, 1473)      # inside the black frame, legend panel removed
ANNOTATED_CROP_BOX = (0, 286, 946, 1762)    # black phone-screenshot bars removed

# Affine transform annotated-crop -> original-crop, found once with SIFT feature
# matching + RANSAC (345 inlier matches, 0.80 px RMS error).  Row-major 2x3.
ANNOTATED_TO_ORIGINAL = (
    (0.9961298410443848, 0.0003612555159231497, 0.8597495958290483),
    (-0.0003612555159231497, 0.9961298410443848, 1.1877127224224295),
)

# Same transform for the LATEST annotated map (purple walkways + 2 extra blue dots):
# 379 inlier matches, 0.81 px RMS error.
LATEST_ANNOTATED_TO_ORIGINAL = (
    (0.9960462036256448, 0.0003716810442615057, 0.8829984637669279),
    (-0.0003716810442615057, 0.9960462036256448, 1.2285423502394914),
)

# A line may come this close (in map pixels) to a building edge without being
# counted as "inside" it.  The footprints come from hand-painted colour blocks,
# so their edges are only accurate to about 2 px.
COLLISION_TOLERANCE_PX = 2.0
SAMPLE_STEP_PX = 1.0

Point = Sequence[float]


# ----------------------------------------------------------------------------
# Part 1 - geometry
# ----------------------------------------------------------------------------
def pct_to_px(p: Point) -> tuple[float, float]:
    """(x%, y%) -> (x px, y px) on the reference map."""
    return p[0] / 100.0 * MAP_WIDTH_PX, p[1] / 100.0 * MAP_HEIGHT_PX


def px_to_pct(p: Point, ndigits: int = 2) -> list[float]:
    """(x px, y px) -> [x%, y%] rounded."""
    return [round(p[0] / MAP_WIDTH_PX * 100.0, ndigits), round(p[1] / MAP_HEIGHT_PX * 100.0, ndigits)]


def polyline_length_px(points_pct: Sequence[Point]) -> float:
    """Length of a polyline given in percent coordinates, measured in map pixels.
    (Percent units can't be used directly because the map is not square.)"""
    pts = [pct_to_px(p) for p in points_pct]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def point_in_polygon(pt: Point, poly: Sequence[Point]) -> bool:
    """Ray-casting test: count how many polygon edges a ray to the right crosses."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def distance_to_polygon_edge(p: Point, poly: Sequence[Point]) -> float:
    return min(point_segment_distance(p, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def sample_polyline(points_px: Sequence[Point], step: float = SAMPLE_STEP_PX) -> Iterable[tuple[float, float]]:
    """Yield points every `step` pixels along the polyline (end points included)."""
    for a, b in zip(points_px, points_px[1:]):
        seg_len = math.dist(a, b)
        n = max(1, int(seg_len / step))
        for i in range(n):
            t = i / n
            yield a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
    if points_px:
        yield tuple(points_px[-1])


def polyline_collisions(points_pct: Sequence[Point], footprints: Sequence[dict],
                        ignore_building_ids: Iterable[str] = (),
                        tolerance_px: float = COLLISION_TOLERANCE_PX) -> list[dict]:
    """Return the footprints that the polyline passes THROUGH.

    A sample point counts as a collision when it is inside a footprint polygon
    AND more than `tolerance_px` away from that polygon's edge.
    `ignore_building_ids` is used for entrance connectors: the first/last few
    pixels of a line that starts at a door may touch that door's own building.
    """
    ignore = set(ignore_building_ids)
    pts_px = [pct_to_px(p) for p in points_pct]
    hits = []
    for fp in footprints:
        if not fp.get("is_obstacle", True):
            continue
        if fp.get("building_id") and fp["building_id"] in ignore:
            continue
        poly = [pct_to_px(p) for p in fp["footprint_coordinates"]]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        bbox = (min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)
        worst = 0.0
        for sp in sample_polyline(pts_px):
            if not (bbox[0] <= sp[0] <= bbox[2] and bbox[1] <= sp[1] <= bbox[3]):
                continue
            if point_in_polygon(sp, poly):
                depth = distance_to_polygon_edge(sp, poly)
                worst = max(worst, depth)
        if worst > tolerance_px:
            hits.append({"footprint_id": fp["footprint_id"], "building_id": fp.get("building_id"),
                         "structure_name": fp.get("structure_name"), "max_depth_px": round(worst, 1)})
    return hits


def entrance_owner_map(entrances: Sequence[dict]) -> dict[str, str]:
    """walkway node id -> building id, for nodes that are building entrances."""
    return {e["node_id"]: e["building_id"] for e in entrances if e.get("node_id")}


def segment_ignore_ids(segment: dict, owners: dict[str, str]) -> set[str]:
    """A segment that ends at a door may touch that door's own building."""
    return {owners[n] for n in (segment["start_node_id"], segment["end_node_id"]) if n in owners}


def validate_walkway_registry(nodes: Sequence[dict], segments: Sequence[dict],
                              footprints: Sequence[dict], entrances: Sequence[dict]) -> dict:
    """Checks run before the walkway model is accepted into SQLite.

    * every segment references existing nodes
    * every segment's first/last vertex sits on its start/end node
    * coordinates are inside 0-100
    * no WALKABLE segment passes through a building footprint
      (a segment stored with is_walkable = false, such as the unverified Quadrangle path,
       is reported as a warning instead and is never used by Dijkstra)
    * distance_m is either NULL or a non-negative number
    """
    errors, warnings = [], []
    node_by_id = {n["node_id"]: n for n in nodes}
    owners = entrance_owner_map(entrances)
    for n in nodes:
        if not (0 <= n["x_percent"] <= 100 and 0 <= n["y_percent"] <= 100):
            errors.append(f"node {n['node_id']} outside the map")
    seen = set()
    for s in segments:
        sid = s["segment_id"]
        if sid in seen:
            errors.append(f"duplicate segment {sid}")
        seen.add(sid)
        for key in ("start_node_id", "end_node_id"):
            if s[key] not in node_by_id:
                errors.append(f"{sid}: unknown {key} {s[key]}")
        geom = s["geometry_coordinates"]
        if len(geom) < 2:
            errors.append(f"{sid}: needs at least 2 points")
            continue
        for end, key in ((geom[0], "start_node_id"), (geom[-1], "end_node_id")):
            nd = node_by_id.get(s[key])
            if nd and math.dist(pct_to_px(end), pct_to_px((nd["x_percent"], nd["y_percent"]))) > 1.0:
                errors.append(f"{sid}: geometry does not start/end on {s[key]}")
        if any(not (0 <= x <= 100 and 0 <= y <= 100) for x, y in geom):
            errors.append(f"{sid}: point outside the map")
        d = s.get("distance_m")
        if d is not None and (not isinstance(d, (int, float)) or d < 0 or math.isnan(d)):
            errors.append(f"{sid}: invalid distance_m {d!r}")
        hits = polyline_collisions(geom, footprints, segment_ignore_ids(s, owners))
        if hits:
            where = ", ".join(h["footprint_id"] for h in hits)
            if s.get("is_walkable", True):
                errors.append(f"{sid}: passes through {where}")
            else:
                # stored for display only (e.g. the unverified Quadrangle path) - never routed
                warnings.append(f"{sid}: drawn over {where}; stored as NOT walkable (excluded from routing)")
    # connectivity report (warnings only)
    adj = {n: set() for n in node_by_id}
    for s in segments:
        if not s.get("is_walkable", True):
            continue
        if s["start_node_id"] in adj and s["end_node_id"] in adj:
            adj[s["start_node_id"]].add(s["end_node_id"])
            adj[s["end_node_id"]].add(s["start_node_id"])
    start = "n_main_gate" if "n_main_gate" in adj else next(iter(adj), None)
    reach, stack = set(), [start] if start else []
    while stack:
        u = stack.pop()
        if u in reach:
            continue
        reach.add(u)
        stack.extend(adj[u] - reach)
    routable = {s[k] for s in segments if s.get("is_walkable", True) for k in ("start_node_id", "end_node_id")}
    for n in node_by_id:
        if n not in reach and n in routable:
            warnings.append(f"node {n} is not connected to the Main Gate")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "nodes": len(nodes), "segments": len(segments), "connected_nodes": len(reach)}


# ----------------------------------------------------------------------------
# Part 2 - image tools (only for prepare_map.py)
# ----------------------------------------------------------------------------
def _pil():
    try:
        from PIL import Image  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise SystemExit("prepare_map.py needs extra packages:  pip install -r requirements-tools.txt") from exc
    from PIL import Image
    import numpy as np
    return Image, np


def crop_original(src_path, dst_path):
    """Cut the map panel (without legend and frame) out of the original image."""
    Image, _ = _pil()
    im = Image.open(src_path).convert("RGB").crop(ORIGINAL_CROP_BOX)
    assert im.size == (MAP_WIDTH_PX, MAP_HEIGHT_PX), im.size
    im.save(dst_path)
    return im


def crop_and_align_annotated(src_path, dst_path, transform=ANNOTATED_TO_ORIGINAL):
    """Remove the black bars and warp an annotated plan onto the original frame.
    Both annotated screenshots share the same crop box; each has its own transform."""
    Image, np = _pil()
    im = Image.open(src_path).convert("RGB").crop(ANNOTATED_CROP_BOX)
    (a, b, c), (d, e, f) = transform
    # Pillow wants the INVERSE mapping (output pixel -> input pixel)
    m = np.array([[a, b, c], [d, e, f], [0, 0, 1]], dtype=float)
    inv = np.linalg.inv(m)
    out = im.transform((MAP_WIDTH_PX, MAP_HEIGHT_PX), Image.AFFINE, tuple(inv[:2].ravel()),
                       resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    out.save(dst_path)
    return out


def detect_annotation_marks(aligned_annotated_path, min_area=25):
    """Find the red / blue / orange / green / purple marks by colour (HSV thresholds).
    Returns {colour: [(area, x%, y%), ...]} sorted by area."""
    Image, np = _pil()
    try:
        from scipy import ndimage
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("--audit needs scipy:  pip install -r requirements-tools.txt") from exc
    im = Image.open(aligned_annotated_path).convert("RGB")
    hsv = np.array(im.convert("HSV")).astype(int)   # Pillow: H,S,V all 0-255
    h, s, v = hsv[..., 0] * 180 // 255, hsv[..., 1], hsv[..., 2]
    masks = {
        "blue": (h >= 98) & (h <= 112) & (s > 120) & (v > 150),
        "red": ((h <= 5) | (h >= 172)) & (s > 150) & (v > 150),
        "orange": (h >= 9) & (h <= 20) & (s > 120) & (v > 170),
        "green": (h >= 45) & (h <= 75) & (s > 120) & (v > 120),
        "purple": (h >= 125) & (h <= 155) & (s > 90) & (v > 120),
    }
    out = {}
    for colour, m in masks.items():
        m = ndimage.binary_opening(m, structure=np.ones((2, 2)))
        lab, n = ndimage.label(m, structure=np.ones((3, 3)))
        comps = []
        for i in range(1, n + 1):
            ys, xs = np.nonzero(lab == i)
            if len(xs) >= min_area:
                comps.append((len(xs), round(xs.mean() / MAP_WIDTH_PX * 100, 2), round(ys.mean() / MAP_HEIGHT_PX * 100, 2)))
        out[colour] = sorted(comps, reverse=True)
        out[colour + "_mask"] = m
    return out
