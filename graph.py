"""
graph.py
--------
The Data Structures and Algorithms part of PSU PathFinder.

Data structures
    Graph.adjacency   dict  { vertex: [Edge, Edge, ...] }      (adjacency list)
    distances         dict  { vertex: best distance so far }
    previous          dict  { vertex: (vertex before it, edge used) }
    visited           set   vertices whose distance is final
    heap              list  managed by heapq = binary MIN-heap of (distance, vertex)

Two graphs are built from SQLite
    1. BUILDING GRAPH  - 14 vertices, one undirected edge per Excel pair,
       weight = shortest MEASURED walking distance in metres.
       -> Dijkstra on this graph gives the distance shown to the user.
    2. WALKWAY GRAPH   - vertices are walkway junctions, door connections and
       end points; edges are traced walkway polylines; weight = drawn length in
       MAP PIXELS (not metres - no segment has been measured on site).
       -> the SAME Dijkstra function picks which traced walkway line to draw
          for each leg of the route.  Its pixel lengths are never shown as metres.

Rule: a route line is only drawn if every point of it follows a traced walkway
and the collision check proves it never passes through a building footprint.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import database
import map_processor

# average walking pace used for the "≈ N min walk" estimate (same value as the original
# Campus Navigator prototype). Only a display estimate - the distance itself is measured.
WALKING_SPEED_M_PER_MIN = 80

# segment types allowed when the Excel option is the MAIN route ("use the main road")
MAIN_ROUTE_TYPES = {"original_walkway", "entrance_access", "main_gate_access"}
# alternative routes may also use the hand-drawn orange paths
ALL_ROUTE_TYPES = MAIN_ROUTE_TYPES | {"alternative_walkway", "junction_link", "verified_pedestrian_connection"}


class RoutingError(Exception):
    """A route request that cannot be answered. `code` is sent to the browser."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------- graph
@dataclass
class Edge:
    target: str
    weight: float
    info: dict = field(default_factory=dict)


class Graph:
    """Weighted graph stored as an adjacency list (dict of lists)."""

    def __init__(self):
        self.adjacency: dict[str, list[Edge]] = {}

    def add_vertex(self, v):
        self.adjacency.setdefault(v, [])

    def add_edge(self, u, v, weight, bidirectional=True, info=None):
        if u == v:
            raise ValueError(f"self-loop on {u}")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or math.isnan(weight) or math.isinf(weight):
            raise ValueError(f"edge {u}-{v}: weight must be a finite number, got {weight!r}")
        if weight < 0:
            raise ValueError(f"edge {u}-{v}: negative weight {weight} (Dijkstra needs weights >= 0)")
        self.add_vertex(u)
        self.add_vertex(v)
        self.adjacency[u].append(Edge(v, float(weight), dict(info or {}, reverse=False)))
        if bidirectional:
            self.adjacency[v].append(Edge(u, float(weight), dict(info or {}, reverse=True)))

    def __contains__(self, v):
        return v in self.adjacency

    def vertices(self):
        return list(self.adjacency)

    def edge_count(self):
        return sum(len(e) for e in self.adjacency.values())

    def neighbors(self, v):
        return self.adjacency.get(v, [])


# ---------------------------------------------------------------- Dijkstra
def dijkstra(graph, sources, targets=None):
    """Dijkstra's shortest-path algorithm with a heapq min-heap.

    sources  - one vertex or a list of vertices (all start at distance 0)
    targets  - optional vertex or set of vertices; the search stops as soon as
               the first of them is popped from the heap (its distance is final)

    Returns a dict: distances, previous, reached (target found or None), stats.
    Complexity: O((V + E) log V).
    """
    if isinstance(sources, str):
        sources = [sources]
    if isinstance(targets, str):
        targets = {targets}
    targets = set(targets) if targets else None

    distances = {v: math.inf for v in graph.adjacency}
    previous = {v: None for v in graph.adjacency}
    visited = set()
    heap = []
    stats = {"heap_pushes": 0, "heap_pops": 0, "edges_examined": 0, "relaxations": 0, "visit_order": []}

    for s in sources:
        if s not in graph:
            raise KeyError(s)
        distances[s] = 0.0
        heapq.heappush(heap, (0.0, s))
        stats["heap_pushes"] += 1

    reached = None
    while heap:
        d, u = heapq.heappop(heap)                 # vertex with the smallest tentative distance
        stats["heap_pops"] += 1
        if u in visited:                           # stale heap entry (lazy deletion)
            continue
        visited.add(u)
        stats["visit_order"].append(u)
        if targets and u in targets:               # its distance can no longer improve
            reached = u
            break
        for edge in graph.neighbors(u):
            stats["edges_examined"] += 1
            if edge.target in visited:
                continue
            new_dist = d + edge.weight
            if new_dist < distances[edge.target]:  # relaxation
                distances[edge.target] = new_dist
                previous[edge.target] = (u, edge)
                heapq.heappush(heap, (new_dist, edge.target))
                stats["heap_pushes"] += 1
                stats["relaxations"] += 1
    stats["vertices_finalised"] = len(visited)
    return {"distances": distances, "previous": previous, "reached": reached, "stats": stats}


def reconstruct_path(previous, target):
    """Follow the predecessor dictionary back from the target.
    Returns [(vertex, edge_used_to_reach_it_or_None), ...] from start to target."""
    path = []
    cur, edge = target, None
    while cur is not None:
        step = previous.get(cur)
        path.append((cur, step[1] if step else None))
        cur = step[0] if step else None
    path.reverse()
    return path


def shortest_path(graph, start, target):
    """Convenience wrapper: (total distance, [vertices], [edges], stats) or raises RoutingError."""
    if start not in graph:
        raise RoutingError("INVALID_START", f"Unknown starting location '{start}'.", 404)
    if target not in graph:
        raise RoutingError("INVALID_DESTINATION", f"Unknown destination '{target}'.", 404)
    if start == target:
        raise RoutingError("SAME_LOCATION", "The starting location and the destination are the same.")
    res = dijkstra(graph, start, target)
    if res["reached"] is None or math.isinf(res["distances"][target]):
        raise RoutingError("UNREACHABLE", "There is no measured walking connection between these two places.", 422)
    steps = reconstruct_path(res["previous"], target)
    return res["distances"][target], [v for v, _ in steps], [e for _, e in steps[1:]], res["stats"]


# ---------------------------------------------------------------- graph 1: buildings (metres)
def build_building_graph(conn, exclude_flagged=False):
    """14 vertices; one undirected edge per pair = the shortest usable measured option."""
    g = Graph()
    for b in database.get_buildings(conn):
        g.add_vertex(b["building_id"])
    best = {}
    for r in database.get_walking_routes(conn):
        if r["distance_m"] is None or r["verification_status"] == "invalid":
            continue
        if exclude_flagged and r["verification_status"] == "flagged":
            continue
        key = frozenset((r["start_building_id"], r["end_building_id"]))
        if key not in best or r["distance_m"] < best[key]["distance_m"]:
            best[key] = r
    for r in best.values():
        g.add_edge(r["start_building_id"], r["end_building_id"], r["distance_m"], bidirectional=True, info={
            "route_id": r["route_id"], "route_type": r["route_type"], "description": r["route_description"],
            "flagged": r["verification_status"] == "flagged", "flag_reason": r["flag_reason"],
            "source_row": r["source_row"], "recorded_from": r["start_building_id"]})
    return g


# ---------------------------------------------------------------- graph 2: walkways (map pixels)
def build_walkway_graph(segments, allowed_types=ALL_ROUTE_TYPES, weight_field="length_map_px", exclude_ids=()):
    """Vertices = walkway nodes, edges = walkable traced segments of the allowed types.
    weight_field='distance_m' would route on measured metres once every segment is measured.
    exclude_ids removes segments that failed the building-collision check."""
    g = Graph()
    for s in segments:
        if not s["is_walkable"] or s["segment_type"] not in allowed_types or s["segment_id"] in exclude_ids:
            continue
        w = s.get(weight_field)
        if w is None:                      # never invent a weight
            continue
        g.add_edge(s["start_node_id"], s["end_node_id"], w, bidirectional=True, info={"segment": s})
    return g


def access_nodes(nodes, building_id):
    """Walkway nodes where a building can be entered or left (doors, frontage, gate)."""
    return [n["node_id"] for n in nodes if n.get("building_id") == building_id]


def _leg_geometry(conn_data, sources, target_building, allowed_types, exclude_ids=()):
    """Run Dijkstra on the walkway graph from `sources` to any access node of the target."""
    nodes, segments = conn_data["nodes"], conn_data["segments"]
    targets = set(access_nodes(nodes, target_building))
    g = build_walkway_graph(segments, allowed_types, exclude_ids=exclude_ids)
    sources = [s for s in sources if s in g]
    targets = {t for t in targets if t in g}
    if not sources or not targets:
        return None
    res = dijkstra(g, sources, targets)
    if res["reached"] is None:
        return None
    steps = reconstruct_path(res["previous"], res["reached"])
    segs = []
    for _, edge in steps[1:]:
        s = edge.info["segment"]
        coords = list(reversed(s["geometry_coordinates"])) if edge.info["reverse"] else list(s["geometry_coordinates"])
        segs.append({"segment_id": s["segment_id"], "segment_name": s["segment_name"], "segment_type": s["segment_type"],
                     "verification_status": s["verification_status"], "distance_m": s["distance_m"],
                     "length_map_px": s["length_map_px"], "path_id": s["path_id"], "coordinates": coords,
                     "start_node_id": s["end_node_id"] if edge.info["reverse"] else s["start_node_id"],
                     "end_node_id": s["start_node_id"] if edge.info["reverse"] else s["end_node_id"]})
    return {"start_node": steps[0][0], "end_node": res["reached"], "segments": segs,
            "length_map_px": round(res["distances"][res["reached"]], 1), "stats": res["stats"]}


def _merge_polyline(segments):
    pts = []
    for s in segments:
        for p in s["coordinates"]:
            if not pts or pts[-1] != p:
                pts.append(p)
    return pts


def _bearing(a, b):
    ax, ay = map_processor.pct_to_px(a)
    bx, by = map_processor.pct_to_px(b)
    return math.atan2(by - ay, bx - ax)


def _turn_word(p_before, p_turn, p_after):
    a1 = _bearing(p_before, p_turn)
    a2 = _bearing(p_turn, p_after)
    diff = math.degrees((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)   # +ve = clockwise on screen = right
    if abs(diff) < 30:
        return "Continue onto"
    if abs(diff) > 150:
        return "Make a U-turn onto"
    side = "right" if diff > 0 else "left"
    return ("Turn " if abs(diff) >= 60 else "Bear ") + side + " onto"


def directions_for_leg(leg_geo, node_names, start_label, end_label, node_types=None):
    """Group consecutive segments with the same name into human steps."""
    if not leg_geo:
        return []
    steps = []
    groups = []
    for s in leg_geo["segments"]:
        if groups and groups[-1]["name"] == s["segment_name"]:
            groups[-1]["segments"].append(s)
        else:
            groups.append({"name": s["segment_name"], "type": s["segment_type"], "segments": [s]})
    def door(node_id):
        name = node_names.get(node_id, "entrance")
        return name[:1].lower() + name[1:]

    for i, gr in enumerate(groups):
        pts = _merge_polyline(gr["segments"])
        if gr["type"] == "entrance_access" and i == 0:
            text = f"Leave {start_label} by the {door(gr['segments'][0]['start_node_id'])}"
        elif gr["type"] == "entrance_access" and i == len(groups) - 1:
            text = f"Walk up to the {door(gr['segments'][-1]['end_node_id'])} of {end_label}"
        elif i == 0:
            text = f"From {start_label}, follow the {gr['name']}"
        else:
            prev_pts = _merge_polyline(groups[i - 1]["segments"])
            word = _turn_word(prev_pts[-2], prev_pts[-1], pts[1]) if len(prev_pts) >= 2 and len(pts) >= 2 else "Continue onto"
            text = f"{word} {gr['name']}"
        steps.append({"text": text, "segment_type": gr["type"],
                      "segment_ids": [s["segment_id"] for s in gr["segments"]],
                      "map_px": round(sum(s["length_map_px"] for s in gr["segments"]), 1)})
    end_node = leg_geo["end_node"]
    if (node_types or {}).get(end_node) == "building_frontage":
        arrive = f"Arrive in front of {end_label} (no door is marked for it on the map)"
    else:
        arrive = f"Arrive at {end_label}"
    steps.append({"text": arrive, "segment_type": "arrival", "segment_ids": [], "map_px": 0})
    return steps


# ---------------------------------------------------------------- the full route request
OPTION_LABELS = {"main": "Main route", "alternative_1": "Alternative route 1", "alternative_2": "Alternative route 2"}


def find_route(conn, start, destination, exclude_flagged=False):
    """Answer one route request. Everything the website shows comes from here."""
    if not start:
        raise RoutingError("MISSING_START", "Please choose a starting location.")
    if not destination:
        raise RoutingError("MISSING_DESTINATION", "Please choose a destination.")
    buildings = {b["building_id"]: b for b in database.get_buildings(conn)}
    if start not in buildings:
        raise RoutingError("INVALID_START", f"Unknown starting location '{start}'.", 404)
    if destination not in buildings:
        raise RoutingError("INVALID_DESTINATION", f"Unknown destination '{destination}'.", 404)

    graph = build_building_graph(conn, exclude_flagged)
    if graph.edge_count() == 0:
        raise RoutingError("NO_DATA", "No measured walking distances are stored in the database.", 503)
    total, route_ids, edges, stats = shortest_path(graph, start, destination)

    # every measured option for each leg (to show alternatives)
    all_routes = database.get_walking_routes(conn)
    options_by_pair = {}
    for r in all_routes:
        options_by_pair.setdefault(frozenset((r["start_building_id"], r["end_building_id"])), []).append(r)

    data = {"nodes": database.get_walkway_nodes(conn), "segments": database.get_walkway_segments(conn)}
    node_names = {n["node_id"]: n["node_name"] for n in data["nodes"]}
    node_types = {n["node_id"]: n["node_type"] for n in data["nodes"]}
    footprints = database.get_footprints(conn)
    entrances = database.get_entrances(conn)
    owners = {e["node_id"]: e["building_id"] for e in entrances if e["node_id"]}
    for n in data["nodes"]:
        if n.get("building_id"):
            owners.setdefault(n["node_id"], n["building_id"])

    legs, warnings, all_segments = [], [], []
    previous_end_node = None
    geometry_complete = True
    for i, edge in enumerate(edges):
        a, b = route_ids[i], route_ids[i + 1]
        info = edge.info
        option_type = info["route_type"]
        options = sorted(options_by_pair.get(frozenset((a, b)), []), key=lambda r: r["route_type"])
        if info["flagged"]:
            warnings.append(f"{buildings[a]['short_name']} → {buildings[b]['short_name']} uses {edge.weight:g} m, "
                            f"which is flagged: {info['flag_reason']}. Tick 'Ignore flagged measurements' to recalculate without it.")

        # --- geometry for this leg ---------------------------------------
        sources = [previous_end_node] if previous_end_node else access_nodes(data["nodes"], a)
        allowed = MAIN_ROUTE_TYPES if option_type == "main" else ALL_ROUTE_TYPES
        geometry_note = None
        rejected = set()            # segments that failed the collision check
        geo = None
        for _attempt in range(6):
            geo = _leg_geometry(data, sources, b, allowed, rejected)
            if geo is None and allowed is MAIN_ROUTE_TYPES:
                geo = _leg_geometry(data, sources, b, ALL_ROUTE_TYPES, rejected)
                if geo:
                    geometry_note = ("The paved walkways of the original map alone do not connect these two doors, "
                                     "so the drawn line also uses an annotated (orange) path.")
            if geo is None:
                break
            bad = [s["segment_id"] for s in geo["segments"]
                   if map_processor.polyline_collisions(
                       s["coordinates"], footprints,
                       {owners[n] for n in (s["start_node_id"], s["end_node_id"]) if n in owners})]
            if not bad:
                break
            rejected.update(bad)    # never draw a line through a building: drop it and search again
            geo = None
        if rejected:
            geometry_note = ((geometry_note + " ") if geometry_note else "") + (
                f"Segment(s) {', '.join(sorted(rejected))} failed the building-collision check and were skipped"
                + ("; another traced walkway was used." if geo else "; no other valid walkway line exists, so none is drawn."))
        if geo is None:
            geometry_complete = False
            previous_end_node = None
            if not geometry_note:
                geometry_note = "No traced walkway connects these two places yet, so no line is drawn for this leg."
        else:
            previous_end_node = geo["end_node"]
            all_segments.extend(geo["segments"])

        uses_inferred = bool(geo) and any(s["verification_status"] == "traced_inferred" for s in geo["segments"])
        uses_annotated = bool(geo) and any(s["segment_type"] in ("alternative_walkway", "junction_link") for s in geo["segments"])
        legs.append({
            "index": i + 1, "from": a, "to": b,
            "from_name": buildings[a]["building_name"], "to_name": buildings[b]["building_name"],
            "distance": round(edge.weight, 2), "route_id": info["route_id"], "option_type": option_type,
            "option_label": OPTION_LABELS[option_type], "description": info["description"] or "",
            "flagged": info["flagged"], "excel_row": info["source_row"],
            "walked_in_recorded_direction": info["recorded_from"] == a,
            "options": [{"route_type": o["route_type"], "label": OPTION_LABELS[o["route_type"]],
                         "distance": o["distance_m"], "description": o["route_description"],
                         "status": o["verification_status"], "selected": o["route_id"] == info["route_id"]}
                        for o in options],
            "geometry": None if geo is None else {
                "start_node": geo["start_node"], "start_node_name": node_names.get(geo["start_node"]),
                "end_node": geo["end_node"], "end_node_name": node_names.get(geo["end_node"]),
                "segment_ids": [s["segment_id"] for s in geo["segments"]],
                "coordinates": _merge_polyline(geo["segments"]),
                "uses_annotated_paths": uses_annotated, "uses_inferred_links": uses_inferred,
                "dijkstra_stats": {k: v for k, v in geo["stats"].items() if k != "visit_order"},
            },
            "geometry_note": geometry_note,
            "directions": directions_for_leg(geo, node_names, buildings[a]["short_name"], buildings[b]["short_name"], node_types),
        })

    direct = None
    if len(route_ids) > 2:
        pair_opts = [o for o in options_by_pair.get(frozenset((start, destination)), [])
                     if o["distance_m"] is not None and o["verification_status"] != "invalid"
                     and not (exclude_flagged and o["verification_status"] == "flagged")]
        if pair_opts:
            best = min(pair_opts, key=lambda o: o["distance_m"])
            direct = {"distance": best["distance_m"], "option_label": OPTION_LABELS[best["route_type"]],
                      "description": best["route_description"], "saving": round(best["distance_m"] - total, 2)}

    geometry = _merge_polyline(all_segments) if geometry_complete else []
    if not geometry_complete:
        warnings.append("Part of this route has no traced walkway line. The distance is still correct; "
                        "only legs with a validated line are drawn.")
    any_inferred = any(l["geometry"] and l["geometry"]["uses_inferred_links"] for l in legs)
    if any_inferred:
        warnings.append("The drawn line includes a short link across a lawn (dashed). It is traced from the map but not clearly paved.")

    description = "; ".join(
        f"{l['from_name']} → {l['to_name']}: {l['distance']:g} m ({l['option_label'].lower()}"
        + (f": {l['description']}" if l['description'] else "") + ")" for l in legs)
    return {
        "success": True, "start": start, "destination": destination,
        "start_name": buildings[start]["building_name"], "destination_name": buildings[destination]["building_name"],
        "distance": round(total, 2), "unit": "meters",
        "walking_time_min": max(1, round(total / WALKING_SPEED_M_PER_MIN)),
        "walking_speed_m_per_min": WALKING_SPEED_M_PER_MIN,
        "route": [{"building_id": r, "building_name": buildings[r]["building_name"], "short_name": buildings[r]["short_name"],
                   "x_percent": buildings[r]["x_percent"], "y_percent": buildings[r]["y_percent"]} for r in route_ids],
        "hops": len(edges), "legs": legs,
        "route_segments": [{k: s[k] for k in ("segment_id", "segment_name", "segment_type", "verification_status", "distance_m", "length_map_px")}
                           for s in all_segments],
        "route_geometry": geometry,
        "leg_geometries": [l["geometry"]["coordinates"] if l["geometry"] else [] for l in legs],
        "route_description": description,
        "geometry_status": "traced" if geometry_complete else ("partial" if all_segments else "none"),
        "geometry_verified": False,
        "geometry_collision_check": "passed" if all_segments else "not_applicable",
        "geometry_explanation": ("The line follows walkways traced from the campus map and has been checked "
                                 "against every building footprint. It is not a field survey, so the distance "
                                 "shown comes from the measured Excel data, not from the line's length."),
        "direct_pair": direct, "exclude_flagged": bool(exclude_flagged), "warnings": warnings,
        "algorithm": {"name": "Dijkstra (heapq min-heap)", "graph_vertices": len(graph.vertices()),
                      "graph_edges": graph.edge_count() // 2, **stats},
    }


def floyd_warshall(graph):
    """Independent all-pairs check used by the tests (O(V^3))."""
    vs = graph.vertices()
    d = {a: {b: (0.0 if a == b else math.inf) for b in vs} for a in vs}
    for u in vs:
        for e in graph.neighbors(u):
            d[u][e.target] = min(d[u][e.target], e.weight)
    for k in vs:
        for i in vs:
            for j in vs:
                if d[i][k] + d[k][j] < d[i][j]:
                    d[i][j] = d[i][k] + d[k][j]
    return d
