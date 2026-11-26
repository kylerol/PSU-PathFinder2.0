"""
data_loader.py
--------------
Reads campus_walking_distances.xlsx, cleans every value, validates the whole
sheet, and imports the result into SQLite (table walking_routes).

The workbook is opened READ-ONLY and is never written to.

Rules applied to the "Pairs" sheet
    1. Building names must match an official name in locations.json.
    2. "61.99 m", "61.99m", "61.99 meters" and 61.99 all become 61.99.
    3. Blank cells and "N/A" mean UNAVAILABLE - never 0.
    4. Zero, negative and unreadable values are rejected (reported, not used).
    5. Values under SUSPICIOUS_BELOW_M metres are FLAGGED for on-site checking.
       They are kept exactly as typed - never changed or deleted.
    6. For every pair the smallest available option (main / alt 1 / alt 2)
       is selected, and its description is kept with it.
    7. Exactly 14 locations and 91 unique unordered pairs are expected.

Run it on its own to print the validation report:
    python data_loader.py
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKBOOK = BASE_DIR / "campus_walking_distances.xlsx"
LOCATIONS_FILE = BASE_DIR / "locations.json"

PAIRS_SHEET = "Pairs"
MATRIX_SHEET = "Matrix"
HOWTO_SHEET = "How to use"
EXPECTED_LOCATIONS = 14
EXPECTED_PAIRS = EXPECTED_LOCATIONS * (EXPECTED_LOCATIONS - 1) // 2   # 91
SUSPICIOUS_BELOW_M = 10.0

UNAVAILABLE_TOKENS = {"", "n/a", "na", "none", "-", "--", "null"}
_DISTANCE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(m|meter|meters|metre|metres)?\s*$", re.IGNORECASE)

# column positions in the Pairs sheet (0-based)
COL_NUM, COL_FROM, COL_TO = 0, 1, 2
OPTION_COLUMNS = (               # (route_type, label, distance column, description column)
    ("main", "Main route", 3, 4),
    ("alternative_1", "Alternative route 1", 5, 6),
    ("alternative_2", "Alternative route 2", 7, 8),
)


class DataError(Exception):
    """Raised when the workbook cannot be used at all."""


# ---------------------------------------------------------------- parsing
def parse_distance(value):
    """Turn one cell into (distance or None, status, reason).

    status is one of: 'measured', 'unavailable', 'invalid'.
    """
    if value is None:
        return None, "unavailable", "blank cell"
    if isinstance(value, bool):
        return None, "invalid", f"unexpected value {value!r}"
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if text.lower() in UNAVAILABLE_TOKENS:
            return None, "unavailable", "N/A" if text else "blank cell"
        if text.startswith("="):
            return None, "unavailable", "formula, no typed value"
        match = _DISTANCE_RE.match(text.replace(",", ""))
        if not match:
            return None, "invalid", f"cannot read {text!r} as metres"
        number = float(match.group(1))
    if math.isnan(number) or math.isinf(number):
        return None, "invalid", "not a finite number"
    if number < 0:
        return None, "invalid", f"negative distance {number}"
    if number == 0:
        return None, "invalid", "zero distance between two different places"
    return round(number, 2), "measured", ""


def clean_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in UNAVAILABLE_TOKENS else text


def load_locations(path=LOCATIONS_FILE):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["locations"]


def _name_index(locations):
    index = {}
    for loc in locations:
        index[loc["building_name"].casefold()] = loc["building_id"]
    return index


def _normalise(name):
    return re.sub(r"[^a-z0-9]", "", name.casefold())


# Legacy spellings found in older sources -> canonical building ID.
# The workbook and older images spell building 8 "PBTI"; the official name is PTBI.
LEGACY_NAME_ALIASES = {"pbti": "ptbi"}


def match_building(name, locations):
    """Return (building_id, exact?) for a workbook name, or (None, False).
    A legacy spelling such as "PBTI" is normalised to its canonical ID ("ptbi")
    and reported as a non-exact match, so it is never stored as a second building."""
    if name is None:
        return None, False
    text = str(name).strip()
    if _normalise(text) in LEGACY_NAME_ALIASES:
        return LEGACY_NAME_ALIASES[_normalise(text)], False
    index = _name_index(locations)
    if text.casefold() in index:
        return index[text.casefold()], True
    loose = {_normalise(loc["building_name"]): loc["building_id"] for loc in locations}
    for loc in locations:
        for alias in loc.get("aliases", []):
            loose.setdefault(_normalise(alias), loc["building_id"])
    bid = loose.get(_normalise(text))
    return (bid, False) if bid else (None, False)


def _name_note(name, bid):
    if _normalise(str(name)) in LEGACY_NAME_ALIASES:
        return f"legacy spelling '{name}' normalised to {bid}"
    return f"'{name}' matched loosely to {bid}"


# ---------------------------------------------------------------- workbook
def _open_workbook(path):
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise DataError("openpyxl is not installed. Run: pip install -r requirements.txt") from exc
    path = Path(path)
    if not path.exists():
        raise DataError(f"Workbook not found: {path}")
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=False)
    except Exception as exc:  # corrupt / not an xlsx
        raise DataError(f"Cannot open {path.name}: {exc}") from exc


def load_pairs(path=DEFAULT_WORKBOOK, locations=None):
    """Read and clean every row of the Pairs sheet.

    Returns a dict:
        pairs     - list of processed pairs (see _process_row)
        problems  - errors that make a row unusable
        notes     - observations about the workbook itself
    """
    locations = locations or load_locations()
    wb = _open_workbook(path)
    try:
        if PAIRS_SHEET not in wb.sheetnames:
            raise DataError(f"Sheet '{PAIRS_SHEET}' is missing (found {wb.sheetnames})")
        ws = wb[PAIRS_SHEET]
        rows = list(ws.iter_rows(values_only=True))
        notes = _workbook_notes(wb, rows)
    finally:
        wb.close()

    if not rows:
        raise DataError("The Pairs sheet is empty")
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    if len(header) < 9 or not header[1].lower().startswith("from") or not header[2].lower().startswith("to"):
        raise DataError(f"Unexpected Pairs header: {header[:9]}")

    pairs, problems = [], []
    for excel_row, row in enumerate(rows[1:], start=2):
        if row is None or all(c is None for c in row[:9]):
            continue
        rec = _process_row(excel_row, row, locations, problems)
        if rec:
            pairs.append(rec)
    return {"pairs": pairs, "problems": problems, "notes": notes}


def _process_row(excel_row, row, locations, problems):
    row = list(row) + [None] * (12 - len(row))
    a_name, b_name = row[COL_FROM], row[COL_TO]
    a_id, a_exact = match_building(a_name, locations)
    b_id, b_exact = match_building(b_name, locations)
    if not a_id or not b_id:
        problems.append({"row": excel_row, "problem": f"unknown building name: {a_name!r} / {b_name!r}"})
        return None
    if a_id == b_id:
        problems.append({"row": excel_row, "problem": f"start and destination are the same ({a_name})"})
        return None
    name_notes = []
    if not a_exact:
        name_notes.append(_name_note(a_name, a_id))
    if not b_exact:
        name_notes.append(_name_note(b_name, b_id))

    options = []
    for route_type, label, d_col, t_col in OPTION_COLUMNS:
        raw = row[d_col]
        distance, status, reason = parse_distance(raw)
        description = clean_text(row[t_col])
        flagged, flag_reason = False, ""
        if status == "measured" and distance < SUSPICIOUS_BELOW_M:
            flagged, flag_reason = True, f"unusually short ({distance} m < {SUSPICIOUS_BELOW_M:g} m) - verify on site"
        if status == "invalid":
            problems.append({"row": excel_row, "problem": f"{label}: {reason}"})
        options.append({
            "route_type": route_type, "label": label,
            "raw_value": None if raw is None else str(raw),
            "distance_m": distance, "status": status, "reason": reason,
            "description": description, "flagged": flagged, "flag_reason": flag_reason,
        })

    valid = [o for o in options if o["status"] == "measured"]
    if not valid:
        problems.append({"row": excel_row, "problem": "no usable distance in any option"})
    selected = min(valid, key=lambda o: o["distance_m"]) if valid else None
    selected_unflagged = min((o for o in valid if not o["flagged"]), key=lambda o: o["distance_m"], default=None)
    return {
        "row": excel_row, "number": row[COL_NUM],
        "start_id": a_id, "end_id": b_id, "start_name": a_name, "end_name": b_name,
        "options": options, "selected": selected, "selected_unflagged": selected_unflagged,
        "name_notes": name_notes,
    }


def _workbook_notes(wb, pair_rows):
    """Things worth telling the user about the workbook itself."""
    notes = []
    if HOWTO_SHEET in wb.sheetnames:
        text = " ".join(str(c) for r in wb[HOWTO_SHEET].iter_rows(values_only=True) for c in r if c)
        if "blank" in text.lower():
            notes.append("The 'How to use' sheet says all distances are blank, but the Pairs sheet already "
                         "contains measured values. The Pairs sheet is used as the source of truth.")
    text_values = sum(1 for r in pair_rows[1:] for c in (r[3] if len(r) > 3 else None,) if isinstance(c, str) and c.strip().lower().endswith("m"))
    if text_values:
        notes.append(f"{text_values} main distances are stored as TEXT (e.g. \"61.99 m\"). Excel's COUNT()/MIN() "
                     "ignore text, so the workbook's own 'Shortest (m)' column, progress counter and Matrix sheet "
                     "stay blank. Python parses the text instead.")
    if MATRIX_SHEET in wb.sheetnames:
        notes.append("The Matrix sheet holds formulas that depend on the blank 'Shortest (m)' column, so its "
                     "off-diagonal cells are empty. Blank matrix cells are NOT treated as 0; the matrix is rebuilt from Pairs.")
    return notes


# ---------------------------------------------------------------- validation
def validate(result, locations=None):
    """Whole-sheet checks. Returns a report dict (also used by the API)."""
    locations = locations or load_locations()
    pairs = result["pairs"]
    ids = {loc["building_id"] for loc in locations}
    seen, duplicates = set(), []
    for p in pairs:
        key = frozenset((p["start_id"], p["end_id"]))
        if key in seen:
            duplicates.append({"row": p["row"], "pair": sorted(key)})
        seen.add(key)
    used = {p["start_id"] for p in pairs} | {p["end_id"] for p in pairs}
    missing_pairs = []
    id_list = sorted(ids)
    for i, a in enumerate(id_list):
        for b in id_list[i + 1:]:
            if frozenset((a, b)) not in seen:
                missing_pairs.append([a, b])
    flagged = [{"row": p["row"], "start_id": p["start_id"], "end_id": p["end_id"],
                "route_type": o["route_type"], "distance_m": o["distance_m"],
                "description": o["description"], "reason": o["flag_reason"]}
               for p in pairs for o in p["options"] if o["flagged"]]
    counts = {
        "locations_in_registry": len(ids),
        "locations_in_workbook": len(used),
        "pairs": len(pairs),
        "unique_pairs": len(seen),
        "main_measured": sum(1 for p in pairs if p["options"][0]["status"] == "measured"),
        "alternatives_measured": sum(1 for p in pairs for o in p["options"][1:] if o["status"] == "measured"),
        "alternative_selected": sum(1 for p in pairs if p["selected"] and p["selected"]["route_type"] != "main"),
    }
    copied = _possible_copied_values(pairs)
    ok = (len(ids) == EXPECTED_LOCATIONS and len(seen) == EXPECTED_PAIRS and not duplicates
          and not missing_pairs and not result["problems"] and used == ids)
    return {"ok": ok, "counts": counts, "duplicates": duplicates, "missing_pairs": missing_pairs,
            "unknown_locations": sorted(used - ids), "flagged": flagged, "possible_copied_values": copied,
            "problems": result["problems"], "notes": result["notes"]}


def _possible_copied_values(pairs):
    """Two places with exactly the same distance to several other places may
    have been copied by mistake (e.g. Teacher's Education and DOST)."""
    dist = {}
    for p in pairs:
        if p["selected"]:
            dist.setdefault(p["start_id"], {})[p["end_id"]] = p["options"][0]["distance_m"]
            dist.setdefault(p["end_id"], {})[p["start_id"]] = p["options"][0]["distance_m"]
    out = []
    keys = sorted(dist)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            same = sorted(c for c in dist[a] if c in dist[b] and c not in (a, b) and dist[a][c] == dist[b][c])
            if len(same) >= 3:
                out.append({"locations": [a, b], "same_distance_to": same})
    return out


def distance_matrix(pairs, ids, use="selected"):
    """14 x 14 matrix rebuilt from Pairs. None = no measured value (never 0)."""
    m = {a: {b: (0.0 if a == b else None) for b in ids} for a in ids}
    for p in pairs:
        opt = p[use]
        if opt:
            m[p["start_id"]][p["end_id"]] = opt["distance_m"]
            m[p["end_id"]][p["start_id"]] = opt["distance_m"]
    return m


# ---------------------------------------------------------------- SQLite import
def routes_for_database(pairs, source_sheet=PAIRS_SHEET):
    """One walking_routes row per AVAILABLE option (blank / N/A options are not stored)."""
    rows = []
    for p in pairs:
        for o in p["options"]:
            if o["status"] == "unavailable":
                continue
            status = "flagged" if o["flagged"] else ("measured" if o["status"] == "measured" else "invalid")
            rows.append({
                "route_id": f"pair{p['row'] - 1:03d}_{o['route_type']}",
                "start_building_id": p["start_id"], "end_building_id": p["end_id"],
                "route_type": o["route_type"], "distance_m": o["distance_m"], "raw_value": o["raw_value"],
                "route_description": o["description"], "verification_status": status,
                "flag_reason": o["flag_reason"] or o["reason"] or None,
                "source_sheet": source_sheet, "source_row": p["row"],
            })
    return rows


def import_workbook_to_db(conn, workbook_path=DEFAULT_WORKBOOK, locations=None):
    """Parse + validate the workbook and upsert it into walking_routes.

    Safe to run many times: route_id is fixed per (row, option), so a second
    import updates rows instead of duplicating them. Rows that disappeared
    from the workbook are removed. Must be called inside a transaction.
    Returns (number of records, report).
    """
    locations = locations or load_locations()
    result = load_pairs(workbook_path, locations)
    report = validate(result, locations)
    if result["problems"] and not result["pairs"]:
        raise DataError("No usable rows in the Pairs sheet")
    rows = routes_for_database(result["pairs"])
    conn.executemany(
        """INSERT INTO walking_routes (route_id, start_building_id, end_building_id, route_type, distance_m,
                                       raw_value, route_description, verification_status, flag_reason,
                                       source_sheet, source_row)
           VALUES (:route_id, :start_building_id, :end_building_id, :route_type, :distance_m, :raw_value,
                   :route_description, :verification_status, :flag_reason, :source_sheet, :source_row)
           ON CONFLICT(route_id) DO UPDATE SET
               start_building_id=excluded.start_building_id, end_building_id=excluded.end_building_id,
               route_type=excluded.route_type, distance_m=excluded.distance_m, raw_value=excluded.raw_value,
               route_description=excluded.route_description, verification_status=excluded.verification_status,
               flag_reason=excluded.flag_reason, source_sheet=excluded.source_sheet, source_row=excluded.source_row""",
        rows)
    keep = [r["route_id"] for r in rows]
    if keep:
        conn.execute(f"DELETE FROM walking_routes WHERE route_id NOT IN ({','.join('?' * len(keep))})", keep)
    # verify what was written against what was read
    stored = conn.execute("SELECT COUNT(*) FROM walking_routes").fetchone()[0]
    if stored != len(rows):
        raise DataError(f"Import check failed: read {len(rows)} options but SQLite holds {stored}")
    return len(rows), report


# ---------------------------------------------------------------- CLI report
def print_report(path=DEFAULT_WORKBOOK):
    locations = load_locations()
    result = load_pairs(path, locations)
    report = validate(result, locations)
    names = {l["building_id"]: l["building_name"] for l in locations}
    print(f"Workbook: {Path(path).name}   ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)")
    print("Status  :", "OK" if report["ok"] else "PROBLEMS FOUND")
    for k, v in report["counts"].items():
        print(f"  {k:<24}{v}")
    print("\nFlagged for on-site verification (kept unchanged):")
    for f in report["flagged"]:
        print(f"  row {f['row']}: {names[f['start_id']]} <-> {names[f['end_id']]}  {f['route_type']} = {f['distance_m']} m  ({f['description']})")
    if report["possible_copied_values"]:
        print("\nPossible copied values:")
        for c in report["possible_copied_values"]:
            print(f"  {c['locations']} have identical distances to {c['same_distance_to']}")
    print("\nWorkbook notes:")
    for n in report["notes"]:
        print("  -", n)
    if report["problems"]:
        print("\nProblems:")
        for p in report["problems"]:
            print(f"  row {p['row']}: {p['problem']}")
    print("\nSample pairs:")
    for p in result["pairs"][:3] + [result["pairs"][6], result["pairs"][46]]:
        s = p["selected"]
        print(f"  {names[p['start_id']]} -> {names[p['end_id']]}: selected {s['distance_m']} m ({s['label']}: {s['description']})")
    return report


if __name__ == "__main__":
    rep = print_report(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WORKBOOK)
    sys.exit(0 if rep["ok"] else 1)
