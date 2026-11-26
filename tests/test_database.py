"""SQLite: creation, imports, duplicate prevention, persistence, errors."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
import database
import graph


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "test.db"
        self.status = database.initialize_database(self.db, ROOT)
        self.conn = database.connect(self.db)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # TEST 1 - initialisation
    def test_tables_created(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("buildings", "building_footprints", "entrances", "walkway_nodes", "walkway_segments",
                  "walking_routes", "alternative_paths", "route_history", "data_imports", "schema_version"):
            self.assertIn(t, names)
        self.assertTrue(self.status["created"])
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    # TEST 2 - buildings
    def test_fourteen_buildings(self):
        rows = database.get_buildings(self.conn)
        self.assertEqual(len(rows), 14)
        self.assertEqual(sum(r["location_type"] == "gate" for r in rows), 1)

    # TEST 3 / 4 / 5 - walking routes
    def test_walking_routes_imported_once(self):
        counts = database.table_counts(self.conn)
        self.assertEqual(counts["walking_routes"], 127)            # 91 main + 36 alternatives
        pairs = self.conn.execute("SELECT COUNT(DISTINCT start_building_id || '|' || end_building_id) FROM walking_routes").fetchone()[0]
        self.assertEqual(pairs, 91)

    def test_distance_parsed_to_real(self):
        r = self.conn.execute("""SELECT distance_m, raw_value, typeof(distance_m) t FROM walking_routes
                                 WHERE start_building_id='eng_bldg_1' AND end_building_id='eng_bldg_2' AND route_type='main'""").fetchone()
        self.assertEqual((r["distance_m"], r["raw_value"], r["t"]), (61.99, "61.99 m", "real"))

    def test_main_and_alternative_preserved(self):
        rows = database.get_walking_routes(self.conn, "eng_bldg_1", "acad_bldg_1")
        got = {r["route_type"]: (r["distance_m"], r["route_description"]) for r in rows}
        self.assertEqual(got["main"], (148.27, "use the main road"))
        self.assertEqual(got["alternative_1"], (104.78, "between acad bldg. 1 and 2"))

    def test_flagged_value_kept(self):
        r = self.conn.execute("""SELECT distance_m, verification_status FROM walking_routes
                                 WHERE route_id='pair047_alternative_1'""").fetchone()
        self.assertEqual((r["distance_m"], r["verification_status"]), (3.0, "flagged"))

    # TEST 6 / 7 / 8 - map data
    def test_entrances_stored(self):
        ents = database.get_entrances(self.conn)
        self.assertEqual(len(ents), 17)
        self.assertEqual(len(database.get_entrances(self.conn, "eng_bldg_1")), 3)

    def test_alternative_paths_unmeasured(self):
        paths = database.get_alternative_paths(self.conn)
        self.assertEqual(len(paths), 3)          # 2 orange paths + the new Quadrangle path
        self.assertTrue(all(p["distance_m"] is None for p in paths))
        segs = [s for s in database.get_walkway_segments(self.conn) if s["segment_type"] == "alternative_walkway"]
        self.assertGreaterEqual(len(segs), 6)
        self.assertTrue(all(s["distance_m"] is None for s in segs))

    def test_walkway_model_stored(self):
        nodes = database.get_walkway_nodes(self.conn)
        segs = database.get_walkway_segments(self.conn)
        ids = {n["node_id"] for n in nodes}
        for s in segs:
            self.assertIn(s["start_node_id"], ids)
            self.assertIn(s["end_node_id"], ids)
            self.assertGreaterEqual(len(s["geometry_coordinates"]), 2)
            self.assertGreater(s["length_map_px"], 0)

    # TEST 9 - Dijkstra reads SQLite, not a hard-coded list
    def test_graph_follows_database(self):
        self.assertEqual(graph.find_route(self.conn, "eng_bldg_1", "eng_bldg_2")["distance"], 61.99)
        with database.transaction(self.conn):
            self.conn.execute("UPDATE walking_routes SET distance_m = 50.0 WHERE route_id = 'pair001_main'")
        self.assertEqual(graph.find_route(self.conn, "eng_bldg_1", "eng_bldg_2")["distance"], 50.0)

    # TEST 10 / 11 - history + persistence
    def test_history_persists_after_reconnect(self):
        hid = database.save_route_history(self.conn, "eng_bldg_1", "ptbi", 70.71, {"route": ["eng_bldg_1", "ptbi"]})
        self.conn.close()
        status = database.initialize_database(self.db, ROOT)          # "restart"
        self.assertFalse(status["created"])
        self.conn = database.connect(self.db)
        hist = database.get_route_history(self.conn)
        self.assertEqual(hist[0]["history_id"], hid)
        self.assertEqual(hist[0]["route_result"]["route"], ["eng_bldg_1", "ptbi"])
        self.assertEqual(database.table_counts(self.conn)["buildings"], 14)

    def test_clear_history(self):
        database.save_route_history(self.conn, "eng_bldg_1", "ptbi", 70.71, {})
        self.assertEqual(database.clear_route_history(self.conn), 1)
        self.assertEqual(database.get_route_history(self.conn), [])

    # TEST 12 - duplicates
    def test_repeated_imports_do_not_duplicate(self):
        before = database.table_counts(self.conn)
        st = database.initialize_database(self.db, ROOT)
        self.assertTrue(all(i["status"] == "unchanged" for i in st["imports"]))
        database.initialize_database(self.db, ROOT, force=True)
        database.initialize_database(self.db, ROOT, force=True)
        after = database.table_counts(self.conn)
        for t in ("buildings", "entrances", "walkway_nodes", "walkway_segments", "walking_routes", "alternative_paths"):
            self.assertEqual(before[t], after[t], t)

    def test_import_is_recorded_with_hash(self):
        rows = database.last_imports(self.conn)
        self.assertEqual({r["import_status"] for r in rows}, {"success"})
        self.assertTrue(all(len(r["file_hash"]) == 64 for r in rows))

    # TEST 13 - errors
    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO entrances (entrance_id, building_id, entrance_name, x_percent, y_percent, "
                              "entrance_type, verification_status) VALUES ('x', 'no_such_building', 'x', 1, 1, 'possible', 'unverified')")

    def test_negative_distance_rejected_by_schema(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE walking_routes SET distance_m = -1 WHERE route_id = 'pair001_main'")

    def test_corrupted_database_is_rebuilt(self):
        self.conn.close()
        self.db.write_bytes(b"this is not a sqlite database" * 50)
        status = database.initialize_database(self.db, ROOT)
        self.assertIsNotNone(status["recovered_from"])
        self.assertTrue((self.tmp / status["recovered_from"]).exists())
        self.conn = database.connect(self.db)
        self.assertEqual(database.table_counts(self.conn)["buildings"], 14)

    def test_bad_map_data_is_refused(self):
        data_dir = self.tmp / "data"
        data_dir.mkdir()
        for name in database.SOURCE_FILES.values():
            shutil.copy(ROOT / name, data_dir / name)
        walk = json.loads((data_dir / "walkways.json").read_text(encoding="utf-8"))
        nodes = {n["node_id"]: n for n in walk["nodes"]}
        # a "shortcut" straight through Engineering Bldg. 1
        walk["segments"].append({
            "segment_id": "bad_shortcut", "start_node_id": "j_road_eng1_west", "end_node_id": "j_road_ptbi_west",
            "distance_m": None, "geometry_coordinates": [[nodes["j_road_eng1_west"]["x_percent"], nodes["j_road_eng1_west"]["y_percent"]],
                                                         [nodes["j_road_ptbi_west"]["x_percent"], nodes["j_road_ptbi_west"]["y_percent"]]],
            "segment_type": "original_walkway", "segment_name": "bad", "source": "test", "verification_status": "test",
            "is_walkable": True, "path_id": None})
        (data_dir / "walkways.json").write_text(json.dumps(walk), encoding="utf-8")
        with self.assertRaises(database.DatabaseError) as ctx:
            database.initialize_database(self.tmp / "other.db", data_dir)
        self.assertIn("fp_eng_bldg_1", str(ctx.exception))

    def test_missing_source_with_existing_data_keeps_running(self):
        data_dir = self.tmp / "data2"
        data_dir.mkdir()
        st = database.initialize_database(self.db, data_dir)            # nothing in data_dir
        self.assertTrue(st["errors"])
        self.assertEqual(database.table_counts(self.conn)["buildings"], 14)


if __name__ == "__main__":
    unittest.main()
