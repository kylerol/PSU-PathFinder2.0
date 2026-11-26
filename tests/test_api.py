"""Flask API and website: every endpoint, the JSON shapes, input validation and the page sections."""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
import graph
from app import create_app


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.app = create_app(db_path=cls.tmp / "api.db", testing=True)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post_route(self, **body):
        return self.client.post("/api/route", json=body)

    def test_index_and_assets(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        for path in ("/static/css/styles.css", "/static/js/app.js", "/static/images/campus_map.png",
                     "/static/images/annotated_campus_map.png"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            r.close()

    def test_get_endpoints(self):
        loc = self.client.get("/api/locations").get_json()
        self.assertEqual(loc["count"], 14)
        self.assertEqual((loc["map"]["width_px"], loc["map"]["height_px"]), (944, 1472))
        self.assertEqual(self.client.get("/api/entrances").get_json()["count"], 17)
        self.assertEqual(self.client.get("/api/entrances?building=ptbi").get_json()["count"], 1)
        self.assertEqual(self.client.get("/api/entrances?building=nope").status_code, 404)
        paths = self.client.get("/api/paths").get_json()
        self.assertEqual(len(paths["annotated_paths"]), 3)
        self.assertEqual(len(paths["measured_alternatives"]), 36)
        walk = self.client.get("/api/walkways").get_json()
        self.assertGreater(len(walk["segments"]), 30)
        self.assertEqual(self.client.get("/api/footprints").get_json()["count"], 20)
        opts = self.client.get("/api/route-options?start=ptbi&destination=eng_bldg_1").get_json()
        self.assertEqual(opts["count"], 2)
        self.assertEqual(self.client.get("/api/route-options").get_json()["count"], 127)
        self.assertEqual(self.client.get("/api/route-options?start=ptbi").status_code, 400)
        rep = self.client.get("/api/data-report").get_json()
        self.assertTrue(rep["workbook"]["ok"])
        self.assertEqual(rep["graph"]["pairs_with_shorter_multi_stop_route"], 21)

    def test_route_response_shape(self):
        r = self.post_route(start="eng_bldg_1", destination="ptbi")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        for key in ("success", "start", "destination", "distance", "unit", "route", "route_segments",
                    "route_geometry", "route_description", "geometry_verified", "legs", "algorithm"):
            self.assertIn(key, d)
        self.assertEqual((d["distance"], d["unit"], d["geometry_verified"]), (70.71, "meters", False))
        self.assertEqual(d["walking_time_min"], round(70.71 / graph.WALKING_SPEED_M_PER_MIN))
        self.assertEqual(d["geometry_status"], "traced")
        self.assertGreater(len(d["route_geometry"]), 1)

    def test_route_errors(self):
        cases = [({"start": "", "destination": "ptbi"}, 400, "MISSING_START"),
                 ({"start": "ptbi"}, 400, "MISSING_DESTINATION"),
                 ({"start": "x", "destination": "ptbi"}, 404, "INVALID_START"),
                 ({"start": "ptbi", "destination": "x"}, 404, "INVALID_DESTINATION"),
                 ({"start": "ptbi", "destination": "ptbi"}, 400, "SAME_LOCATION")]
        for body, status, code in cases:
            r = self.post_route(**body)
            self.assertEqual((r.status_code, r.get_json()["error"]), (status, code), body)
        r = self.client.post("/api/route", data="not json", content_type="text/plain")
        self.assertEqual(r.get_json()["error"], "BAD_REQUEST")
        self.assertEqual(self.client.get("/api/route").status_code, 405)
        self.assertEqual(self.client.get("/api/nothing").status_code, 404)

    def test_history_saved_and_optional(self):
        self.client.delete("/api/history")
        self.post_route(start="main_gate", destination="sac")
        self.post_route(start="main_gate", destination="dost", save_history=False)
        h = self.client.get("/api/history").get_json()
        self.assertEqual(h["count"], 1)
        self.assertEqual(h["history"][0]["destination_location_id"], "sac")
        self.assertEqual(self.client.delete("/api/history").get_json()["deleted"], 1)

    def test_history_survives_app_restart(self):
        self.post_route(start="acad_bldg_1", destination="ptbi")
        app2 = create_app(db_path=self.tmp / "api.db", testing=True)
        h = app2.test_client().get("/api/history").get_json()
        self.assertTrue(any(x["destination_location_id"] == "ptbi" for x in h["history"]))

    def test_refresh_data_no_duplicates(self):
        before = self.client.get("/api/data-report").get_json()["table_counts"]
        r = self.client.post("/api/refresh-data", json={"force": True}).get_json()
        self.assertTrue(r["success"])
        for t in ("buildings", "walking_routes", "walkway_segments", "entrances"):
            self.assertEqual(before[t], r["table_counts"][t])


class ApiContractTests(unittest.TestCase):
    """The frontend only renders what these endpoints return, so their content is checked here."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.app = create_app(db_path=cls.tmp / "contract.db", testing=True)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.get_json()

    def test_official_registry_and_ptbi(self):
        locs = {l["building_id"]: l for l in self.get("/api/locations")["locations"]}
        self.assertEqual(len(locs), 14)
        self.assertEqual(locs["ptbi"]["building_name"], "PTBI")
        self.assertNotIn("pbti", locs)
        self.assertEqual(locs["ptbi"]["building_color"].upper(), "#FAC02E")
        self.assertEqual(locs["main_gate"]["building_color"], "#000000")

    def test_no_legacy_spelling_in_any_public_response(self):
        for url in ("/api/locations", "/api/entrances", "/api/walkways", "/api/paths", "/api/footprints",
                    "/api/route-options"):
            text = self.client.get(url).get_data(as_text=True)
            self.assertNotIn("pbti", text, url)
            self.assertNotIn("PBTI", text.replace('"PBTI"', ""), url)     # only allowed as a search alias

    def test_walkways_keep_purple_and_orange_separate(self):
        segs = self.get("/api/walkways")["segments"]
        for s in segs:
            if s["annotation_colour"] == "purple":
                self.assertEqual(s["segment_type"], "original_walkway")
            if s["annotation_colour"] == "orange":
                self.assertEqual(s["segment_type"], "alternative_walkway")

    def test_quadrangle_path_is_returned_as_unverified(self):
        paths = {p["path_id"]: p for p in self.get("/api/paths")["annotated_paths"]}
        q = paths["alt_quadrangle_01"]
        self.assertEqual(q["verification_status"], "unverified")
        self.assertIsNone(q["distance_m"])
        segs = [s for s in self.get("/api/walkways")["segments"] if s["path_id"] == "alt_quadrangle_01"]
        self.assertEqual(len(segs), 4)
        self.assertTrue(all(s["is_walkable"] == 0 for s in segs))

    def test_route_response_shape(self):
        r = self.client.post("/api/route", json={"start": "eng_bldg_1", "destination": "ptbi", "save_history": False})
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        for key in ("success", "start", "destination", "distance", "unit", "route", "route_segments",
                    "route_geometry", "route_description", "geometry_verified"):
            self.assertIn(key, d)
        self.assertEqual(d["unit"], "meters")
        self.assertEqual(d["distance"], 70.71)                      # measured alternative ("exit at back")
        self.assertIsInstance(d["route_geometry"], list)
        self.assertGreater(len(d["route_geometry"]), 1)
        self.assertNotIn("s_quad_", json.dumps(d["route_segments"]))

    def test_route_rejects_bad_json(self):
        r = self.client.post("/api/route", data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["success"])

    def test_page_sections(self):
        html = self.client.get("/").get_data(as_text=True)
        for anchor in ('id="home"', 'id="navigate"', 'id="history"', 'id="about"', 'id="team"'):
            self.assertIn(anchor, html)
        for label in ("Home", "Campus Map", "About Project", "Our Team", "Route History"):
            self.assertIn(f">{label}</a>", html)
        self.assertNotIn("Data notes", html)                          # removed from the public page
        self.assertNotIn('id="dataNotes"', html)
        for name in ("Matthew David Q. Caoile", "Frederick Michael D. Cabanilla", "Keizer Alexus G. Paltep",
                     "Frankie Mae M. Damian", "Jholan Froid Andrei P. Ramirez"):
            self.assertIn(name, html)
        photos = re.findall(r'src="(/static/images/proponent_photos/[^"]+)"', html)
        self.assertEqual(len(photos), 5)
        for p in photos:
            r = self.client.get(p)
            self.assertEqual(r.status_code, 200, p)
            r.close()
        self.assertIn("Quadrangle alternative path", html)          # legend entry

    def test_no_external_cdn(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIsNone(re.search(r'<(script|link)[^>]+(src|href)="https?://', html))


if __name__ == "__main__":
    unittest.main()
