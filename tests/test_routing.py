"""Dijkstra on small graphs, on the campus graph, and through the Flask API."""
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
import database
import graph
from app import create_app


def small_graph(edges, vertices=()):
    g = graph.Graph()
    for v in vertices:
        g.add_vertex(v)
    for u, v, w in edges:
        g.add_edge(u, v, w)
    return g


class SmallGraphTests(unittest.TestCase):
    """The five test kinds named in the proposal, on graphs small enough to check by hand."""

    def test_direct_route(self):
        g = small_graph([("A", "B", 5)])
        dist, path, _, _ = graph.shortest_path(g, "A", "B")
        self.assertEqual((dist, path), (5, ["A", "B"]))

    def test_multi_edge_route_beats_direct_edge(self):
        g = small_graph([("A", "B", 10), ("A", "C", 3), ("C", "B", 4)])
        dist, path, edges, _ = graph.shortest_path(g, "A", "B")
        self.assertEqual((dist, path), (7, ["A", "C", "B"]))
        self.assertEqual([e.weight for e in edges], [3, 4])

    def test_two_routes_different_totals(self):
        g = small_graph([("S", "X", 2), ("X", "T", 2), ("S", "Y", 1), ("Y", "T", 5)])
        self.assertEqual(graph.shortest_path(g, "S", "T")[1], ["S", "X", "T"])

    def test_invalid_location(self):
        g = small_graph([("A", "B", 1)])
        with self.assertRaises(graph.RoutingError) as c:
            graph.shortest_path(g, "A", "Z")
        self.assertEqual(c.exception.code, "INVALID_DESTINATION")

    def test_unreachable(self):
        g = small_graph([("A", "B", 1), ("C", "D", 1)])
        with self.assertRaises(graph.RoutingError) as c:
            graph.shortest_path(g, "A", "D")
        self.assertEqual(c.exception.code, "UNREACHABLE")

    def test_same_location(self):
        g = small_graph([("A", "B", 1)])
        with self.assertRaises(graph.RoutingError) as c:
            graph.shortest_path(g, "A", "A")
        self.assertEqual(c.exception.code, "SAME_LOCATION")

    def test_bad_weights_rejected(self):
        g = graph.Graph()
        for w in (-1, math.nan, math.inf, "5", None, True):
            with self.assertRaises(ValueError):
                g.add_edge("A", "B", w)

    def test_multi_source_and_target_set(self):
        g = small_graph([("a1", "x", 5), ("a2", "x", 1), ("x", "b1", 1), ("x", "b2", 3)])
        res = graph.dijkstra(g, ["a1", "a2"], {"b1", "b2"})
        self.assertEqual(res["reached"], "b1")
        self.assertEqual(res["distances"]["b1"], 2)
        self.assertEqual([v for v, _ in graph.reconstruct_path(res["previous"], "b1")], ["a2", "x", "b1"])

    def test_heap_statistics(self):
        g = small_graph([("A", "B", 1), ("B", "C", 1), ("A", "C", 5)])
        res = graph.dijkstra(g, "A", "C")
        st = res["stats"]
        self.assertGreaterEqual(st["heap_pushes"], 3)
        self.assertEqual(st["visit_order"][0], "A")
        self.assertEqual(st["visit_order"][-1], "C")


class CampusRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        database.initialize_database(cls.tmp / "t.db", ROOT)
        cls.conn = database.connect(cls.tmp / "t.db")
        cls.g = graph.build_building_graph(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def route(self, a, b, **kw):
        return graph.find_route(self.conn, a, b, **kw)

    def test_graph_size(self):
        self.assertEqual(len(self.g.vertices()), 14)
        self.assertEqual(self.g.edge_count(), 182)        # 91 undirected edges stored both ways

    def test_samples(self):
        self.assertEqual(self.route("eng_bldg_1", "eng_bldg_2")["distance"], 61.99)
        r = self.route("eng_bldg_1", "acad_bldg_1")
        self.assertEqual((r["distance"], r["legs"][0]["option_type"]), (104.78, "alternative_1"))
        self.assertEqual(self.route("eng_bldg_1", "ptbi")["distance"], 70.71)

    def test_multi_leg_route_found(self):
        r = self.route("main_gate", "engineering_research_bldg")
        self.assertEqual(r["distance"], 212.73)
        self.assertEqual([s["building_id"] for s in r["route"]], ["main_gate", "eng_bldg_2", "engineering_research_bldg"])
        self.assertEqual(r["direct_pair"]["distance"], 253.04)
        self.assertAlmostEqual(r["direct_pair"]["saving"], 40.31, places=2)

    def test_main_gate_as_start_and_destination(self):
        a = self.route("main_gate", "ptbi")
        b = self.route("ptbi", "main_gate")
        self.assertEqual(a["distance"], b["distance"])
        self.assertEqual([x["building_id"] for x in a["route"]], [x["building_id"] for x in reversed(b["route"])])

    def test_matches_floyd_warshall_for_every_pair(self):
        for exclude in (False, True):
            g = graph.build_building_graph(self.conn, exclude_flagged=exclude)
            fw = graph.floyd_warshall(g)
            for a in g.vertices():
                for b in g.vertices():
                    if a != b:
                        d = graph.shortest_path(g, a, b)[0]
                        self.assertAlmostEqual(d, fw[a][b], places=6, msg=f"{a}->{b}")

    def test_legs_add_up_and_never_longer_than_direct(self):
        for a in self.g.vertices():
            for b in self.g.vertices():
                if a == b:
                    continue
                r = self.route(a, b)
                self.assertAlmostEqual(sum(l["distance"] for l in r["legs"]), r["distance"], places=2)
                direct = min(e.weight for e in self.g.neighbors(a) if e.target == b)
                self.assertLessEqual(r["distance"], direct + 1e-9)

    def test_exclude_flagged(self):
        self.assertEqual(self.route("college_arts_education", "new_engineering_bldg")["distance"], 3.0)
        self.assertEqual(self.route("college_arts_education", "new_engineering_bldg", exclude_flagged=True)["distance"], 49.77)
        r = self.route("teachers_education", "dost", exclude_flagged=True)
        self.assertEqual([s["building_id"] for s in r["route"]], ["teachers_education", "school_canteen", "dost"])
        self.assertEqual(r["distance"], 48.24)

    def test_flagged_route_warns(self):
        r = self.route("college_arts_education", "new_engineering_bldg")
        self.assertTrue(any("flagged" in w for w in r["warnings"]))

    def test_entrances_and_orange_paths_are_not_distance_vertices(self):
        vs = set(self.g.vertices())
        self.assertFalse(any(v.startswith(("n_", "j_", "f_")) for v in vs))
        self.assertEqual(len(vs), 14)

    def test_errors(self):
        for a, b, code in (("", "ptbi", "MISSING_START"), ("ptbi", "", "MISSING_DESTINATION"),
                           ("nope", "ptbi", "INVALID_START"), ("ptbi", "nope", "INVALID_DESTINATION"),
                           ("ptbi", "ptbi", "SAME_LOCATION")):
            with self.assertRaises(graph.RoutingError) as c:
                self.route(a, b)
            self.assertEqual(c.exception.code, code)


if __name__ == "__main__":
    unittest.main()
