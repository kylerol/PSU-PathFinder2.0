"""Excel parsing, validation and registry consistency."""
import json
import re
import unittest

from tests import ROOT
import data_loader as dl
import database


def load(name):
    with open(ROOT / name, encoding="utf-8") as fh:
        return json.load(fh)


class ParseDistanceTests(unittest.TestCase):
    def test_text_with_unit(self):
        self.assertEqual(dl.parse_distance("61.99 m"), (61.99, "measured", ""))
        self.assertEqual(dl.parse_distance("61.99m")[0], 61.99)
        self.assertEqual(dl.parse_distance(" 128.4 meters ")[0], 128.4)

    def test_numbers(self):
        self.assertEqual(dl.parse_distance(70.71)[0], 70.71)
        self.assertEqual(dl.parse_distance(3)[0], 3.0)

    def test_unavailable_is_not_zero(self):
        for v in (None, "", "N/A", "n/a", "  NA ", "-"):
            d, status, _ = dl.parse_distance(v)
            self.assertIsNone(d, v)
            self.assertEqual(status, "unavailable")

    def test_invalid_values_rejected(self):
        for v in ("-5 m", "abc", "0 m", 0, float("nan"), "=MIN(D2,F2)", True):
            d, status, _ = dl.parse_distance(v)
            self.assertIsNone(d, v)
            self.assertIn(status, ("invalid", "unavailable"))


class WorkbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.locations = dl.load_locations()
        cls.result = dl.load_pairs(dl.DEFAULT_WORKBOOK, cls.locations)
        cls.report = dl.validate(cls.result, cls.locations)
        cls.by_pair = {frozenset((p["start_id"], p["end_id"])): p for p in cls.result["pairs"]}

    def pair(self, a, b):
        return self.by_pair[frozenset((a, b))]

    def test_fourteen_locations_and_91_unique_pairs(self):
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["counts"]["locations_in_workbook"], 14)
        self.assertEqual(self.report["counts"]["pairs"], 91)
        self.assertEqual(self.report["counts"]["unique_pairs"], 91)
        self.assertEqual(self.report["duplicates"], [])
        self.assertEqual(self.report["missing_pairs"], [])

    def test_official_names_match_exactly(self):
        """Every workbook name is an official name, except the legacy spelling PBTI."""
        for p in self.result["pairs"]:
            for note in p["name_notes"]:
                self.assertEqual(note, "legacy spelling 'PBTI' normalised to ptbi", p["row"])

    def test_pbti_is_normalised_to_ptbi(self):
        locs = self.locations
        self.assertEqual(dl.match_building("PBTI", locs), ("ptbi", False))
        self.assertEqual(dl.match_building("PTBI", locs), ("ptbi", True))
        ids = {p["start_id"] for p in self.result["pairs"]} | {p["end_id"] for p in self.result["pairs"]}
        self.assertIn("ptbi", ids)
        self.assertNotIn("pbti", ids)
        n = sum(1 for p in self.result["pairs"] if "ptbi" in (p["start_id"], p["end_id"]))
        self.assertEqual(n, 13)                     # one record per partner, no duplicate building

    def test_sample_eng1_eng2(self):
        p = self.pair("eng_bldg_1", "eng_bldg_2")
        self.assertEqual(p["selected"]["distance_m"], 61.99)
        self.assertEqual(p["selected"]["route_type"], "main")

    def test_sample_eng1_acad1_alternative_selected(self):
        p = self.pair("eng_bldg_1", "acad_bldg_1")
        self.assertEqual(p["options"][0]["distance_m"], 148.27)
        self.assertEqual(p["options"][1]["distance_m"], 104.78)
        self.assertEqual(p["selected"]["distance_m"], 104.78)
        self.assertEqual(p["selected"]["description"], "between acad bldg. 1 and 2")

    def test_sample_eng1_ptbi(self):
        p = self.pair("eng_bldg_1", "ptbi")
        self.assertEqual((p["options"][0]["distance_m"], p["selected"]["distance_m"]), (128.4, 70.71))

    def test_alternative_only_selected_when_shorter(self):
        p = self.pair("eng_bldg_2", "teachers_education")      # main 135.75 < alt 156.86
        self.assertEqual(p["selected"]["route_type"], "main")

    def test_three_metre_value_flagged_not_changed(self):
        p = self.pair("college_arts_education", "new_engineering_bldg")
        alt = p["options"][1]
        self.assertEqual(alt["distance_m"], 3.0)
        self.assertTrue(alt["flagged"])
        self.assertEqual(p["selected_unflagged"]["distance_m"], 49.77)
        flagged = {(f["start_id"], f["end_id"], f["distance_m"]) for f in self.report["flagged"]}
        self.assertIn(("college_arts_education", "new_engineering_bldg", 3.0), flagged)

    def test_no_zero_distances(self):
        for p in self.result["pairs"]:
            for o in p["options"]:
                self.assertNotEqual(o["distance_m"], 0)

    def test_na_alternatives_are_unavailable(self):
        p = self.pair("eng_bldg_1", "eng_bldg_2")
        self.assertEqual(p["options"][1]["status"], "unavailable")
        self.assertIsNone(p["options"][1]["distance_m"])

    def test_workbook_inconsistency_reported(self):
        joined = " ".join(self.report["notes"])
        self.assertIn("How to use", joined)
        self.assertIn("TEXT", joined)

    def test_matrix_blank_is_none_not_zero(self):
        ids = [l["building_id"] for l in self.locations]
        m = dl.distance_matrix(self.result["pairs"], ids)
        self.assertEqual(m["ptbi"]["ptbi"], 0.0)
        self.assertEqual(m["eng_bldg_1"]["ptbi"], 70.71)
        self.assertTrue(all(m[a][b] is None or m[a][b] > 0 for a in ids for b in ids if a != b))

    def test_routes_for_database_rows(self):
        rows = dl.routes_for_database(self.result["pairs"])
        self.assertEqual(len(rows), 91 + 36)
        self.assertEqual(len({r["route_id"] for r in rows}), len(rows))

    def test_workbook_is_not_modified(self):
        before = database.file_sha256(dl.DEFAULT_WORKBOOK)
        dl.load_pairs(dl.DEFAULT_WORKBOOK, self.locations)
        self.assertEqual(before, database.file_sha256(dl.DEFAULT_WORKBOOK))

    def test_missing_workbook_raises(self):
        with self.assertRaises(dl.DataError):
            dl.load_pairs(ROOT / "does_not_exist.xlsx", self.locations)


class RegistryTests(unittest.TestCase):
    EXPECTED = {
        "eng_bldg_1": ("Engineering Bldg. 1 (Quadrangle)", "#2F7D32"), "eng_bldg_2": ("Engineering Bldg. 2", "#1B3A7A"),
        "acad_bldg_1": ("Acad Bldg. 1", "#00887A"), "acad_bldg_2_sas": ("Acad Bldg. 2 (SAS)", "#7A1FA2"),
        "college_arts_education": ("College of Arts and Education Bldg.", "#D81A60"),
        "new_engineering_bldg": ("New Engineering Bldg.", "#F67C01"),
        "engineering_research_bldg": ("Engineering Research Bldg.", "#6D4D42"), "ptbi": ("PTBI", "#FAC02E"),
        "school_canteen": ("School Canteen", "#D32F2E"), "teachers_education": ("Teacher's Education", "#28B6F6"),
        "dost": ("DOST", "#9CCC66"), "amphitheater": ("Amphitheater", "#757575"),
        "sac": ("Student's Activity Center (SAC)", "#817716"), "main_gate": ("Main Gate", "#000000"),
    }

    def test_canonical_ids_names_colours(self):
        locs = load("locations.json")["locations"]
        got = {l["building_id"]: (l["building_name"], l["building_color"]) for l in locs}
        self.assertEqual(got, self.EXPECTED)

    def test_coordinates_in_range_and_near_brief(self):
        for l in load("locations.json")["locations"]:
            self.assertTrue(0 <= l["x_percent"] <= 100 and 0 <= l["y_percent"] <= 100)
            # recalculated centroids stay within ~1.5 % of the brief's initial values
            self.assertLess(abs(l["x_percent"] - l["brief_x_percent"]), 1.5, l["building_id"])
            self.assertLess(abs(l["y_percent"] - l["brief_y_percent"]), 1.5, l["building_id"])

    def test_single_main_gate(self):
        locs = load("locations.json")["locations"]
        self.assertEqual(sum(l["building_id"] == "main_gate" for l in locs), 1)
        self.assertEqual(sum("gate" == l["type"] for l in locs), 1)

    def test_entrances_reference_valid_buildings(self):
        ids = {l["building_id"] for l in load("locations.json")["locations"]}
        ents = load("entrances.json")["entrances"]
        self.assertEqual(len(ents), 17)
        self.assertEqual(len({e["entrance_id"] for e in ents}), 17)
        self.assertEqual(sum(e["building_id"] == "eng_bldg_2" for e in ents), 3)   # 2 added on the latest map
        for e in ents:
            self.assertIn(e["building_id"], ids)
            self.assertIn(e["verification_status"], ("visually_matched", "unverified"))

    @unittest.skipUnless((ROOT / "static").exists(), "frontend files not in this package")
    def test_no_coordinates_or_ids_hardcoded_in_frontend(self):
        """One shared coordinate source: the JS/CSS/HTML must not contain building IDs or map percentages."""
        js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
        html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        for l in load("locations.json")["locations"]:
            self.assertNotIn(f"'{l['building_id']}'", js)
            self.assertNotIn(f'"{l["building_id"]}"', js)
            for text in (js, css, html):
                self.assertNotIn(f"{l['x_percent']}%", text)
        self.assertIsNone(re.search(r"(left|top)\s*:\s*\d+(\.\d+)?%", html))

    @unittest.skipUnless((ROOT / "static").exists(), "frontend files not in this package")
    def test_mobile_layout_present(self):
        """Phone map mode: viewport meta, floating search bar, bottom sheet, touch handling."""
        html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
        js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        self.assertIn("viewport-fit=cover", html)
        for needle in ('id="mTopbar"', 'id="sheetHandle"', "data-open-map", "manifest.json"):
            self.assertIn(needle, html)
        self.assertIn("@media (max-width: 820px), (max-height: 520px) and (pointer: coarse)", css)
        for fn in ("function enterMapMode", "function setSheet", "pinch"):
            self.assertIn(fn, js)
        manifest = json.loads((ROOT / "static/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["display"], "standalone")


if __name__ == "__main__":
    unittest.main()
