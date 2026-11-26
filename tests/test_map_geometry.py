"""Map alignment, walkway model, and the no-building-crossing rule."""
import json
import math
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
import database
import graph
import map_processor as mp


def load(name):
    with open(ROOT / name, encoding="utf-8") as fh:
        return json.load(fh)


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    return struct.unpack(">II", head[16:24])


class RegistryGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.walk = load("walkways.json")
        cls.fps = load("footprints.json")["footprints"]
        cls.ents = load("entrances.json")["entrances"]
        cls.locs = {l["building_id"]: l for l in load("locations.json")["locations"]}
        cls.nodes = {n["node_id"]: n for n in cls.walk["nodes"]}

    @unittest.skipUnless((ROOT / "static").exists(), "frontend files not in this package")
    def test_images_share_one_frame(self):
        self.assertEqual(png_size(ROOT / "static/images/campus_map.png"), (mp.MAP_WIDTH_PX, mp.MAP_HEIGHT_PX))
        self.assertEqual(png_size(ROOT / "static/images/annotated_campus_map.png"), (mp.MAP_WIDTH_PX, mp.MAP_HEIGHT_PX))
        self.assertEqual(png_size(ROOT / "static/images/latest_annotated_campus_map.png"), (mp.MAP_WIDTH_PX, mp.MAP_HEIGHT_PX))

    def test_source_images_untouched(self):
        self.assertEqual(png_size(ROOT / "source_images/campus_map_original.png"), (1604, 1474))
        self.assertEqual(png_size(ROOT / "source_images/campus_map_annotated.png"), (946, 2048))
        self.assertEqual(png_size(ROOT / "source_images/campus_map_latest_annotated.png"), (946, 2048))

    def test_registry_validates(self):
        rep = mp.validate_walkway_registry(self.walk["nodes"], self.walk["segments"], self.fps, self.ents)
        self.assertTrue(rep["ok"], rep["errors"])
        # every routable node is reachable from the Main Gate; the only warnings are the
        # display-only Quadrangle segments that are drawn over Eng'g Bldg. 1
        self.assertTrue(rep["warnings"])
        for w in rep["warnings"]:
            self.assertTrue(w.startswith("s_quad_") and "NOT walkable" in w, w)

    def test_no_segment_passes_through_a_building(self):
        owners = mp.entrance_owner_map(self.ents)
        for s in self.walk["segments"]:
            if not s["is_walkable"]:
                continue                     # display-only (unverified Quadrangle path) - tested below
            hits = mp.polyline_collisions(s["geometry_coordinates"], self.fps, mp.segment_ignore_ids(s, owners))
            self.assertEqual(hits, [], s["segment_id"])

    def test_straight_line_between_buildings_is_rejected(self):
        """The check really works: a centre-to-centre line Eng 1 -> PTBI crosses both buildings."""
        a, b = self.locs["eng_bldg_1"], self.locs["ptbi"]
        hits = mp.polyline_collisions([[a["x_percent"], a["y_percent"]], [b["x_percent"], b["y_percent"]]], self.fps)
        self.assertEqual({h["building_id"] for h in hits} >= {"eng_bldg_1", "ptbi"}, True)
        g, c = self.locs["main_gate"], self.locs["engineering_research_bldg"]
        self.assertTrue(mp.polyline_collisions([[g["x_percent"], g["y_percent"]], [c["x_percent"], c["y_percent"]]], self.fps))

    def test_point_in_polygon(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(mp.point_in_polygon((5, 5), sq))
        self.assertFalse(mp.point_in_polygon((15, 5), sq))
        self.assertAlmostEqual(mp.distance_to_polygon_edge((5, 2), sq), 2)

    def test_segments_join_at_shared_nodes(self):
        for s in self.walk["segments"]:
            for end, key in ((s["geometry_coordinates"][0], "start_node_id"), (s["geometry_coordinates"][-1], "end_node_id")):
                n = self.nodes[s[key]]
                self.assertLess(math.dist(mp.pct_to_px(end), mp.pct_to_px((n["x_percent"], n["y_percent"]))), 1.0)

    def test_orange_annotation_is_a_real_modelled_path(self):
        alt = {p["path_id"]: p for p in self.walk["alternative_paths"]}
        self.assertEqual(set(alt), {"ap_acad1_loop", "ap_eng1_back", "alt_quadrangle_01"})
        for pid, p in alt.items():
            segs = [s for s in self.walk["segments"] if s["path_id"] == pid and s["segment_type"] == "alternative_walkway"]
            self.assertTrue(segs, pid)
            self.assertIsNone(p["distance_m"])
            for s in segs:
                self.assertIsNone(s["distance_m"])
                self.assertIn(s["start_node_id"], self.nodes)
                self.assertIn(s["end_node_id"], self.nodes)

    def test_main_gate_node_on_red_dot(self):
        gate = self.locs["main_gate"]
        node = self.nodes["n_main_gate"]
        self.assertLess(math.dist(mp.pct_to_px((gate["x_percent"], gate["y_percent"])), mp.pct_to_px((node["x_percent"], node["y_percent"]))), 1)
        self.assertEqual(node["building_id"], "main_gate")

    def test_every_location_has_an_access_node(self):
        for bid in self.locs:
            self.assertTrue(graph.access_nodes(self.walk["nodes"], bid), bid)

    def test_entrance_nodes_sit_on_their_blue_dots(self):
        for e in self.ents:
            if e["node_id"]:
                n = self.nodes[e["node_id"]]
                self.assertEqual((n["x_percent"], n["y_percent"]), (e["x_percent"], e["y_percent"]))

    def test_coordinates_round_trip(self):
        for p in ([0, 0], [50, 50], [22.97, 72.11], [100, 100]):
            self.assertEqual(mp.px_to_pct(mp.pct_to_px(p)), [round(p[0], 2), round(p[1], 2)])


class RouteGeometryTests(unittest.TestCase):
    """Every one of the 182 route answers draws a line that follows walkways and avoids buildings."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        database.initialize_database(cls.tmp / "g.db", ROOT)
        cls.conn = database.connect(cls.tmp / "g.db")
        cls.segs = {s["segment_id"]: s for s in database.get_walkway_segments(cls.conn)}
        cls.fps = database.get_footprints(cls.conn)
        cls.nodes = {n["node_id"]: n for n in database.get_walkway_nodes(cls.conn)}
        ids = [b["building_id"] for b in database.get_buildings(cls.conn)]
        cls.routes = [graph.find_route(cls.conn, a, b) for a in ids for b in ids if a != b]

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_routes_have_traced_geometry(self):
        self.assertEqual(len(self.routes), 182)
        for r in self.routes:
            self.assertEqual(r["geometry_status"], "traced", (r["start"], r["destination"]))

    def test_every_drawn_point_lies_on_a_walkway_segment(self):
        def on_some_segment(p):
            q = mp.pct_to_px(p)
            for s in self.segs.values():
                pts = [mp.pct_to_px(x) for x in s["geometry_coordinates"]]
                if any(mp.point_segment_distance(q, a, b) < 0.6 for a, b in zip(pts, pts[1:])):
                    return True
            return False
        for r in self.routes[::7]:
            for leg in r["legs"]:
                for p in leg["geometry"]["coordinates"]:
                    self.assertTrue(on_some_segment(p), (r["start"], r["destination"], p))

    def test_drawn_routes_never_cross_buildings(self):
        owners = {n["node_id"]: n["building_id"] for n in self.nodes.values() if n["building_id"]}
        for r in self.routes:
            for leg in r["legs"]:
                for sid in leg["geometry"]["segment_ids"]:
                    s = self.segs[sid]
                    hits = mp.polyline_collisions(s["geometry_coordinates"], self.fps, mp.segment_ignore_ids(s, owners))
                    self.assertEqual(hits, [], (r["start"], r["destination"], sid))

    def test_route_is_continuous_and_starts_at_doors_not_centres(self):
        for r in self.routes:
            legs = r["legs"]
            for i, leg in enumerate(legs):
                geo = leg["geometry"]
                self.assertEqual(self.nodes[geo["start_node"]]["building_id"] if i == 0 else None,
                                 r["start"] if i == 0 else None)
                self.assertEqual(self.nodes[geo["end_node"]]["building_id"], leg["to"])
                self.assertIn(self.nodes[geo["end_node"]]["node_type"],
                              ("building_entrance_connection", "building_frontage", "main_gate_connection"))
                if i:
                    self.assertEqual(geo["start_node"], legs[i - 1]["geometry"]["end_node"])
            # the line never starts at the building centre marker
            first = legs[0]["geometry"]["coordinates"][0]
            centre = (r["route"][0]["x_percent"], r["route"][0]["y_percent"])
            if r["start"] != "main_gate":
                self.assertGreater(math.dist(mp.pct_to_px(first), mp.pct_to_px(centre)), 5)

    def test_main_route_legs_prefer_original_walkways(self):
        r = graph.find_route(self.conn, "main_gate", "amphitheater")
        types = {self.segs[s]["segment_type"] for s in r["legs"][0]["geometry"]["segment_ids"]}
        self.assertTrue(types <= graph.MAIN_ROUTE_TYPES)

    def test_unmeasured_segments_never_give_metres(self):
        r = graph.find_route(self.conn, "eng_bldg_1", "ptbi")
        self.assertTrue(all(s["distance_m"] is None for s in r["route_segments"]))
        self.assertEqual(r["distance"], 70.71)       # comes from the Excel graph only

    def test_invalid_geometry_is_not_drawn(self):
        """If a stored segment were moved through a building, the route must drop the line, not draw it."""
        tmp_db = self.tmp / "broken.db"
        shutil.copy(self.tmp / "g.db", tmp_db)
        c = database.connect(tmp_db)
        try:
            seg = database.get_walkway_segments(c)
            s = next(x for x in seg if x["segment_id"] == "s_ptbi_rd_02")
            a = self.nodes[s["start_node_id"]]
            b = self.nodes[s["end_node_id"]]
            ptbi = next(x for x in database.get_buildings(c) if x["building_id"] == "ptbi")
            bad = [[a["x_percent"], a["y_percent"]], [ptbi["x_percent"], ptbi["y_percent"]], [b["x_percent"], b["y_percent"]]]
            with database.transaction(c):
                c.execute("UPDATE walkway_segments SET geometry_coordinates = ? WHERE segment_id = ?",
                          (json.dumps(bad), "s_ptbi_rd_02"))
            r = graph.find_route(c, "eng_bldg_1", "ptbi")
            self.assertEqual(r["distance"], 70.71)                       # distance is unaffected
            leg = r["legs"][0]
            self.assertIn("collision", leg["geometry_note"])
            self.assertIn("s_ptbi_rd_02", leg["geometry_note"])
            # the broken segment is never drawn; a different valid walkway is used instead
            self.assertNotIn("s_ptbi_rd_02", leg["geometry"]["segment_ids"])
            segs = {x["segment_id"]: x for x in database.get_walkway_segments(c)}
            owners = {n["node_id"]: n["building_id"] for n in database.get_walkway_nodes(c) if n["building_id"]}
            for sid in leg["geometry"]["segment_ids"]:
                x = segs[sid]
                self.assertEqual(mp.polyline_collisions(x["geometry_coordinates"], self.fps, mp.segment_ignore_ids(x, owners)), [])
        finally:
            c.close()


class WalkwayClassificationTests(unittest.TestCase):
    """Latest annotated map: purple = ORIGINAL walkway, orange = ALTERNATIVE walkway."""

    @classmethod
    def setUpClass(cls):
        cls.walk = load("walkways.json")
        cls.segs = {s["segment_id"]: s for s in cls.walk["segments"]}

    def test_purple_segments_are_original_walkways(self):
        purple = [s for s in self.walk["segments"] if s["annotation_colour"] == "purple"]
        self.assertGreaterEqual(len(purple), 7)
        for s in purple:
            self.assertEqual(s["segment_type"], "original_walkway", s["segment_id"])
            self.assertIsNone(s["path_id"], s["segment_id"])          # not part of any alternative path
            self.assertEqual(s["source"], "latest_annotated_campus_map.png")
            self.assertIsNone(s["distance_m"])

    def test_orange_segments_are_alternative_walkways(self):
        orange = [s for s in self.walk["segments"] if s["annotation_colour"] == "orange"]
        self.assertGreaterEqual(len(orange), 6)
        for s in orange:
            self.assertEqual(s["segment_type"], "alternative_walkway", s["segment_id"])
            self.assertIsNotNone(s["path_id"])
            self.assertIsNone(s["distance_m"])

    def test_purple_and_orange_never_mixed(self):
        alt_ids = {sid for p in self.walk["alternative_paths"] for sid in p["segment_ids"]}
        for s in self.walk["segments"]:
            if s["annotation_colour"] == "purple":
                self.assertNotIn(s["segment_id"], alt_ids)
            if s["segment_type"] == "original_walkway":
                self.assertNotEqual(s["annotation_colour"], "orange")

    def test_reclassified_strokes_recorded(self):
        notes = {a["id"]: a for a in self.walk["other_annotations"]}
        rec = notes["reclassified_by_latest_map"]
        for sid in rec["segments"]:
            self.assertEqual(self.segs[sid]["annotation_colour"], "purple")

    def test_new_eng2_entrances_connect_to_main_road(self):
        ents = {e["entrance_id"]: e for e in load("entrances.json")["entrances"]}
        for eid in ("eng2_entrance_north", "eng2_entrance_west"):
            self.assertEqual(ents[eid]["building_id"], "eng_bldg_2")
            node = ents[eid]["node_id"]
            linked = [s for s in self.walk["segments"] if node in (s["start_node_id"], s["end_node_id"])]
            self.assertTrue(linked)
            other = {s["start_node_id"] if s["end_node_id"] == node else s["end_node_id"] for s in linked}
            road = [s for s in self.walk["segments"] if s["segment_name"] == "Main campus road"
                    and (s["start_node_id"] in other or s["end_node_id"] in other)]
            self.assertTrue(road, eid)

    def test_main_route_legs_can_use_purple_walkways(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            database.initialize_database(tmp / "c.db", ROOT)
            c = database.connect(tmp / "c.db")
            # New Eng'g Bldg. is reached by the purple walkway from the gate plaza, a MAIN-route leg
            r = graph.find_route(c, "main_gate", "new_engineering_bldg")
            leg = r["legs"][0]
            self.assertEqual(leg["option_type"], "main")
            self.assertIsNone(leg["geometry_note"])        # no fallback to orange needed any more
            self.assertIn("s_pw_gate_neb", leg["geometry"]["segment_ids"])
            c.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class QuadrangleAlternativePathTests(unittest.TestCase):
    """NEW orange path around the courtyard of Eng'g Bldg. 1 (latest map, image(3))."""

    @classmethod
    def setUpClass(cls):
        cls.walk = load("walkways.json")
        cls.fps = load("footprints.json")["footprints"]
        cls.path = next(p for p in cls.walk["alternative_paths"] if p["path_id"] == "alt_quadrangle_01")
        cls.segs = [s for s in cls.walk["segments"] if s["path_id"] == "alt_quadrangle_01"]
        cls.tmp = Path(tempfile.mkdtemp())
        database.initialize_database(cls.tmp / "q.db", ROOT)
        cls.conn = database.connect(cls.tmp / "q.db")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_recorded_as_its_own_alternative_path(self):
        self.assertEqual(self.path["verification_status"], "unverified")
        self.assertIsNone(self.path["distance_m"])                      # never invented from pixels
        self.assertEqual(len(self.segs), 4)
        self.assertEqual(len(self.path["path_coordinates"]), 4)

    def test_segments_are_orange_alternative_and_not_walkable(self):
        for s in self.segs:
            self.assertEqual(s["segment_type"], "alternative_walkway")
            self.assertEqual(s["annotation_colour"], "orange")
            self.assertEqual(s["verification_status"], "unverified")
            self.assertFalse(s["is_walkable"])
            self.assertIsNone(s["distance_m"])

    def test_drawn_over_the_quadrangle_building(self):
        """The stroke lies inside the Eng'g Bldg. 1 footprint, which is exactly why it is not routed."""
        inside = [s for s in self.segs if any(h["building_id"] == "eng_bldg_1"
                                                for h in mp.polyline_collisions(s["geometry_coordinates"], self.fps))]
        self.assertGreaterEqual(len(inside), 3)

    def test_stored_in_sqlite(self):
        paths = {p["path_id"]: p for p in database.get_alternative_paths(self.conn)}
        self.assertIn("alt_quadrangle_01", paths)
        self.assertIsNone(paths["alt_quadrangle_01"]["distance_m"])
        stored = [s for s in database.get_walkway_segments(self.conn, walkable_only=False) if s["path_id"] == "alt_quadrangle_01"]
        self.assertEqual(len(stored), 4)
        self.assertTrue(all(s["is_walkable"] == 0 for s in stored))

    def test_excluded_from_dijkstra(self):
        g = graph.build_walkway_graph(database.get_walkway_segments(self.conn, walkable_only=False), graph.ALL_ROUTE_TYPES)
        used = {e.info["segment"]["segment_id"] for edges in g.adjacency.values() for e in edges}
        self.assertTrue(used)
        self.assertFalse(any(sid.startswith("s_quad_") for sid in used))
        for a, b in (("eng_bldg_1", "ptbi"), ("eng_bldg_2", "ptbi"), ("main_gate", "eng_bldg_1")):
            r = graph.find_route(self.conn, a, b)
            ids = {s["segment_id"] for s in r["route_segments"]}
            self.assertFalse(any(i.startswith("s_quad_") for i in ids), (a, b))


if __name__ == "__main__":
    unittest.main()
