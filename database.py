"""
database.py
-----------
All SQLite work for PSU PathFinder (Python's built-in sqlite3 module).

The database file lives at  instance/psu_pathfinder.db  and is the website's
persistent memory:

    buildings            14 official locations
    building_footprints  restricted areas (route lines may not pass through them)
    entrances            possible doors (blue dots)
    walkway_nodes        junctions / end points / door connections of the walkway network
    walkway_segments     walkway polylines between two nodes
    walking_routes       measured main + alternative distances from the Excel workbook
    alternative_paths    the orange hand-drawn paths (grouped)
    route_history        every successful route search
    data_imports         which source files were imported (with SHA-256 hash)
    schema_version       migration bookkeeping

Startup behaviour (initialize_database):
    * creates the file and tables only if they are missing
    * never deletes route history
    * re-imports a source file ONLY if its content hash changed
    * if the file is corrupted it is renamed to *.corrupt-<time> and rebuilt
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import map_processor

log = logging.getLogger("psu_pathfinder.database")

BASE_DIR = Path(__file__).resolve().parent
LOCAL_DB_PATH = BASE_DIR / "instance" / "psu_pathfinder.db"


def configured_db_path():
    """Where the SQLite file lives, in this order:
    1. DATABASE_PATH                      (Render: /var/data/psu_pathfinder.db, on the persistent disk)
    2. RAILWAY_VOLUME_MOUNT_PATH/...      (only if you host on Railway instead)
    3. instance/psu_pathfinder.db         (your own computer)"""
    explicit = os.environ.get("DATABASE_PATH") or os.environ.get("PSU_PATHFINDER_DB")
    if explicit:
        return Path(explicit)
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        return Path(volume) / "psu_pathfinder.db"
    return LOCAL_DB_PATH


def _mount_point(path):
    """The mount point (a separately attached disk/volume) that contains `path`, or None."""
    for parent in [path, *path.parents]:
        if parent == Path(parent.anchor):
            return None
        if parent.exists() and os.path.ismount(parent):
            return str(parent)
    return None


def storage_status(db_path):
    """Is the database on persistent storage? Used by /healthz and the startup log.
    On Render a persistent disk is a separate mount (e.g. /var/data); everything else
    in the container is wiped on every deploy or restart."""
    db_path = Path(db_path).resolve()
    on_cloud = bool(os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("DYNO"))
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("PERSISTENT_DISK_PATH")
    if volume:
        persistent = Path(volume).resolve() in db_path.parents
        mount = volume
    elif on_cloud:
        mount = _mount_point(db_path.parent)
        persistent = mount is not None
    else:
        mount = None
        persistent = True                  # a laptop's disk is persistent
    return {"path": str(db_path), "persistent": persistent, "cloud": on_cloud, "volume_mount": mount}


DEFAULT_DB_PATH = configured_db_path()
SCHEMA_VERSION = 4

SOURCE_FILES = {
    "locations": "locations.json",
    "footprints": "footprints.json",
    "walkways": "walkways.json",
    "entrances": "entrances.json",
    "workbook": "campus_walking_distances.xlsx",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS buildings (
    building_id     TEXT PRIMARY KEY,
    building_name   TEXT NOT NULL UNIQUE,
    short_name      TEXT,
    legend_number   INTEGER UNIQUE,
    building_color  TEXT NOT NULL,
    x_percent       REAL NOT NULL CHECK (x_percent BETWEEN 0 AND 100),
    y_percent       REAL NOT NULL CHECK (y_percent BETWEEN 0 AND 100),
    description     TEXT,
    aliases         TEXT NOT NULL DEFAULT '[]',   -- JSON list
    plan_label      TEXT,
    location_type   TEXT NOT NULL DEFAULT 'building' CHECK (location_type IN ('building', 'gate'))
);

CREATE TABLE IF NOT EXISTS building_footprints (
    footprint_id            TEXT PRIMARY KEY,
    building_id             TEXT REFERENCES buildings(building_id) ON UPDATE CASCADE,  -- NULL = unnamed structure
    structure_name          TEXT,
    footprint_coordinates   TEXT NOT NULL,          -- JSON [[x%, y%], ...]
    verification_status     TEXT NOT NULL,
    source_image            TEXT,
    is_obstacle             INTEGER NOT NULL DEFAULT 1 CHECK (is_obstacle IN (0, 1))
);

CREATE TABLE IF NOT EXISTS walkway_nodes (
    node_id             TEXT PRIMARY KEY,
    node_name           TEXT NOT NULL,
    node_type           TEXT NOT NULL CHECK (node_type IN ('walkway_intersection', 'path_junction',
                         'main_gate_connection', 'building_entrance_connection', 'walkway_endpoint', 'building_frontage')),
    x_percent           REAL NOT NULL CHECK (x_percent BETWEEN 0 AND 100),
    y_percent           REAL NOT NULL CHECK (y_percent BETWEEN 0 AND 100),
    verification_status TEXT NOT NULL,
    building_id         TEXT REFERENCES buildings(building_id) ON UPDATE CASCADE   -- set for doors / frontage / gate
);

CREATE TABLE IF NOT EXISTS alternative_paths (
    path_id               TEXT PRIMARY KEY,
    path_name             TEXT NOT NULL,
    start_location        TEXT,
    end_location          TEXT,
    path_coordinates      TEXT NOT NULL,        -- JSON list of polylines
    distance_m            REAL CHECK (distance_m IS NULL OR distance_m >= 0),
    verification_status   TEXT NOT NULL,
    description           TEXT,
    possible_excel_match  TEXT
);

CREATE TABLE IF NOT EXISTS walkway_segments (
    segment_id            TEXT PRIMARY KEY,
    start_node_id         TEXT NOT NULL REFERENCES walkway_nodes(node_id),
    end_node_id           TEXT NOT NULL REFERENCES walkway_nodes(node_id),
    distance_m            REAL CHECK (distance_m IS NULL OR distance_m >= 0),   -- NULL = not measured
    length_map_px         REAL NOT NULL CHECK (length_map_px >= 0),             -- drawing length, NOT metres
    geometry_coordinates  TEXT NOT NULL,        -- JSON [[x%, y%], ...] ordered start -> end
    segment_type          TEXT NOT NULL CHECK (segment_type IN ('original_walkway', 'alternative_walkway',
                           'main_gate_access', 'verified_pedestrian_connection', 'entrance_access', 'junction_link')),
    segment_name          TEXT,
    source                TEXT,
    annotation_colour     TEXT CHECK (annotation_colour IS NULL OR annotation_colour IN ('purple', 'orange')),
    verification_status   TEXT NOT NULL,
    is_walkable           INTEGER NOT NULL DEFAULT 1 CHECK (is_walkable IN (0, 1)),
    path_id               TEXT REFERENCES alternative_paths(path_id),
    note                  TEXT,
    CHECK (start_node_id <> end_node_id)
);

CREATE TABLE IF NOT EXISTS entrances (
    entrance_id           TEXT PRIMARY KEY,
    building_id           TEXT NOT NULL REFERENCES buildings(building_id) ON UPDATE CASCADE,
    entrance_name         TEXT NOT NULL,
    x_percent             REAL NOT NULL CHECK (x_percent BETWEEN 0 AND 100),
    y_percent             REAL NOT NULL CHECK (y_percent BETWEEN 0 AND 100),
    entrance_type         TEXT NOT NULL,
    verification_status   TEXT NOT NULL,
    node_id               TEXT REFERENCES walkway_nodes(node_id),     -- NULL = display only
    note                  TEXT
);

CREATE TABLE IF NOT EXISTS walking_routes (
    route_id              TEXT PRIMARY KEY,
    start_building_id     TEXT NOT NULL REFERENCES buildings(building_id) ON UPDATE CASCADE,
    end_building_id       TEXT NOT NULL REFERENCES buildings(building_id) ON UPDATE CASCADE,
    route_type            TEXT NOT NULL CHECK (route_type IN ('main', 'alternative_1', 'alternative_2')),
    distance_m            REAL CHECK (distance_m IS NULL OR distance_m > 0),
    raw_value             TEXT,
    route_description     TEXT,
    verification_status   TEXT NOT NULL CHECK (verification_status IN ('measured', 'flagged', 'invalid')),
    flag_reason           TEXT,
    source_sheet          TEXT NOT NULL,
    source_row            INTEGER NOT NULL,
    UNIQUE (start_building_id, end_building_id, route_type),
    CHECK (start_building_id <> end_building_id)
);

CREATE TABLE IF NOT EXISTS route_history (
    history_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start_location_id       TEXT NOT NULL REFERENCES buildings(building_id) ON UPDATE CASCADE,
    destination_location_id TEXT NOT NULL REFERENCES buildings(building_id) ON UPDATE CASCADE,
    total_distance_m        REAL NOT NULL CHECK (total_distance_m >= 0),
    route_result            TEXT NOT NULL,          -- JSON summary of the result
    search_timestamp        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_imports (
    import_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_filename   TEXT NOT NULL,
    file_hash         TEXT NOT NULL,
    imported_at       TEXT NOT NULL,
    import_status     TEXT NOT NULL CHECK (import_status IN ('success', 'failed', 'skipped')),
    records_imported  INTEGER NOT NULL DEFAULT 0,
    import_notes      TEXT
);

CREATE INDEX IF NOT EXISTS idx_routes_pair   ON walking_routes(start_building_id, end_building_id);
CREATE INDEX IF NOT EXISTS idx_segments_ends ON walkway_segments(start_node_id, end_node_id);
CREATE INDEX IF NOT EXISTS idx_history_time  ON route_history(search_timestamp);
CREATE INDEX IF NOT EXISTS idx_imports_file  ON data_imports(source_filename, file_hash);
"""


class DatabaseError(Exception):
    """Raised for problems the website should report to the user."""


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- connections
def connect(db_path=DEFAULT_DB_PATH):
    """Open a connection with foreign keys ON and rows usable like dicts."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Cannot open the database at {db_path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn):
    """BEGIN ... COMMIT, or ROLLBACK if anything inside fails."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- schema / migration
def create_tables(conn):
    """Create missing tables; upgrade an older database.  Returns True when a
    migration ran (the caller then re-imports every source file once)."""
    conn.executescript(SCHEMA)
    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    if current < SCHEMA_VERSION:
        migrate(conn, current)
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (SCHEMA_VERSION, utc_now()))
        conn.commit()
        return current > 0
    return False


def migrate(conn, from_version):
    """Upgrade older databases without losing data.
    v1 -> v2 added walkway_nodes.building_id and walkway_segments.note.
    v2 -> v3 added walkway_segments.annotation_colour (purple = original, orange = alternative).
    v3 -> v4 PTBI rename + alternative_walkway segment type (see _migrate_v4).
    After any migration every source file is re-imported once (initialize_database)."""
    if 0 < from_version < 3:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(walkway_nodes)")}
        if "building_id" not in cols:
            conn.execute("ALTER TABLE walkway_nodes ADD COLUMN building_id TEXT REFERENCES buildings(building_id)")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(walkway_segments)")}
        if "note" not in cols:
            conn.execute("ALTER TABLE walkway_segments ADD COLUMN note TEXT")
        if "annotation_colour" not in cols:
            conn.execute("ALTER TABLE walkway_segments ADD COLUMN annotation_colour TEXT")
    if 0 < from_version < 4:
        _migrate_v4(conn)


# (table, column) pairs that hold a building ID
_BUILDING_ID_COLUMNS = (
    ("buildings", "building_id"), ("building_footprints", "building_id"), ("walkway_nodes", "building_id"),
    ("entrances", "building_id"), ("walking_routes", "start_building_id"), ("walking_routes", "end_building_id"),
    ("route_history", "start_location_id"), ("route_history", "destination_location_id"),
)


def _migrate_v4(conn):
    """v3 -> v4:
    * building 8 renamed: ID 'pbti' -> 'ptbi', name 'PBTI' -> 'PTBI' (route history is kept and rewritten)
    * walkway_segments.segment_type 'annotated_alternative_walkway' -> 'alternative_walkway'
      (the CHECK list changed, so the table is rebuilt; it is re-imported from walkways.json)"""
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with transaction(conn):
            for table, col in _BUILDING_ID_COLUMNS:
                conn.execute(f"UPDATE {table} SET {col} = 'ptbi' WHERE {col} = 'pbti'")
            conn.execute("UPDATE buildings SET building_name = 'PTBI', short_name = 'PTBI' WHERE building_id = 'ptbi'")
            conn.execute("UPDATE route_history SET route_result = "
                         "REPLACE(REPLACE(route_result, 'pbti', 'ptbi'), 'PBTI', 'PTBI')")
            conn.execute("DROP TABLE IF EXISTS walkway_segments")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)          # recreates walkway_segments with the new CHECK list


def check_integrity(db_path):
    """Return None if the file is a healthy SQLite database, else an error message."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        return None if result == "ok" else f"integrity_check: {result}"
    except sqlite3.DatabaseError as exc:
        return str(exc)


# ---------------------------------------------------------------- import bookkeeping
def already_imported(conn, filename, file_hash):
    row = conn.execute(
        "SELECT 1 FROM data_imports WHERE source_filename = ? AND file_hash = ? AND import_status = 'success' LIMIT 1",
        (filename, file_hash)).fetchone()
    return row is not None


def record_import(conn, filename, file_hash, status, records, notes=""):
    conn.execute(
        "INSERT INTO data_imports (source_filename, file_hash, imported_at, import_status, records_imported, import_notes) "
        "VALUES (?, ?, ?, ?, ?, ?)", (filename, file_hash, utc_now(), status, records, notes[:2000]))


def last_imports(conn):
    rows = conn.execute(
        """SELECT d.* FROM data_imports d
           JOIN (SELECT source_filename, MAX(import_id) AS mid FROM data_imports GROUP BY source_filename) last
             ON last.mid = d.import_id ORDER BY d.source_filename""").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- inserts / upserts
def _upsert(conn, table, pk, rows, columns):
    if not rows:
        return 0
    cols = ", ".join(columns)
    params = ", ".join(f":{c}" for c in columns)
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c != pk)
    conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({params}) ON CONFLICT({pk}) DO UPDATE SET {updates}", rows)
    return len(rows)


def _delete_missing(conn, table, pk, keep_ids):
    keep_ids = list(keep_ids)
    if keep_ids:
        conn.execute(f"DELETE FROM {table} WHERE {pk} NOT IN ({','.join('?' * len(keep_ids))})", keep_ids)
    else:
        conn.execute(f"DELETE FROM {table}")


def insert_buildings(conn, locations):
    rows = [{
        "building_id": l["building_id"], "building_name": l["building_name"], "short_name": l.get("short_name"),
        "legend_number": l.get("legend_number"), "building_color": l["building_color"],
        "x_percent": l["x_percent"], "y_percent": l["y_percent"], "description": l.get("description"),
        "aliases": json.dumps(l.get("aliases", []), ensure_ascii=False), "plan_label": l.get("plan_label"),
        "location_type": l.get("type", "building"),
    } for l in locations]
    return _upsert(conn, "buildings", "building_id", rows, list(rows[0].keys()))


def insert_footprints(conn, footprints):
    rows = [{
        "footprint_id": f["footprint_id"], "building_id": f.get("building_id"), "structure_name": f.get("structure_name"),
        "footprint_coordinates": json.dumps(f["footprint_coordinates"]), "verification_status": f["verification_status"],
        "source_image": f.get("source_image"), "is_obstacle": 1 if f.get("is_obstacle", True) else 0,
    } for f in footprints]
    n = _upsert(conn, "building_footprints", "footprint_id", rows, list(rows[0].keys()))
    _delete_missing(conn, "building_footprints", "footprint_id", [r["footprint_id"] for r in rows])
    return n


def insert_walkway_nodes(conn, nodes):
    rows = [{k: n.get(k) for k in ("node_id", "node_name", "node_type", "x_percent", "y_percent",
                                   "verification_status", "building_id")} for n in nodes]
    return _upsert(conn, "walkway_nodes", "node_id", rows, list(rows[0].keys()))


def insert_alternative_paths(conn, paths):
    rows = [{
        "path_id": p["path_id"], "path_name": p["path_name"], "start_location": p.get("start_location"),
        "end_location": p.get("end_location"), "path_coordinates": json.dumps(p["path_coordinates"]),
        "distance_m": p.get("distance_m"), "verification_status": p["verification_status"],
        "description": p.get("description"), "possible_excel_match": p.get("possible_excel_match"),
    } for p in paths]
    return _upsert(conn, "alternative_paths", "path_id", rows, list(rows[0].keys()))


def insert_walkway_segments(conn, segments):
    rows = [{
        "segment_id": s["segment_id"], "start_node_id": s["start_node_id"], "end_node_id": s["end_node_id"],
        "distance_m": s.get("distance_m"),
        "length_map_px": round(map_processor.polyline_length_px(s["geometry_coordinates"]), 1),
        "geometry_coordinates": json.dumps(s["geometry_coordinates"]), "segment_type": s["segment_type"],
        "segment_name": s.get("segment_name"), "source": s.get("source"), "annotation_colour": s.get("annotation_colour"),
        "verification_status": s["verification_status"],
        "is_walkable": 1 if s.get("is_walkable", True) else 0, "path_id": s.get("path_id"), "note": s.get("note"),
    } for s in segments]
    return _upsert(conn, "walkway_segments", "segment_id", rows, list(rows[0].keys()))


def insert_entrances(conn, entrances):
    rows = [{k: e.get(k) for k in ("entrance_id", "building_id", "entrance_name", "x_percent", "y_percent",
                                   "entrance_type", "verification_status", "node_id", "note")} for e in entrances]
    return _upsert(conn, "entrances", "entrance_id", rows, list(rows[0].keys()))


def import_map_registries(conn, base_dir=BASE_DIR):
    """Validate the four JSON registries together, then write them in FK order."""
    base_dir = Path(base_dir)
    locations = _load_json(base_dir / SOURCE_FILES["locations"])["locations"]
    footprints = _load_json(base_dir / SOURCE_FILES["footprints"])["footprints"]
    walk = _load_json(base_dir / SOURCE_FILES["walkways"])
    entrances = _load_json(base_dir / SOURCE_FILES["entrances"])["entrances"]

    ids = {l["building_id"] for l in locations}
    problems = []
    if len(ids) != 14 or len(locations) != 14:
        problems.append(f"expected 14 unique locations, found {len(ids)}")
    if sum(1 for l in locations if l["building_id"] == "main_gate") != 1:
        problems.append("there must be exactly one main_gate")
    for e in entrances:
        if e["building_id"] not in ids:
            problems.append(f"entrance {e['entrance_id']} -> unknown building {e['building_id']}")
    for f in footprints:
        if f.get("building_id") and f["building_id"] not in ids:
            problems.append(f"footprint {f['footprint_id']} -> unknown building {f['building_id']}")
    check = map_processor.validate_walkway_registry(walk["nodes"], walk["segments"], footprints, entrances)
    problems += check["errors"]
    if problems:
        raise DatabaseError("Map data failed validation: " + "; ".join(problems))

    n = insert_buildings(conn, locations)
    n += insert_footprints(conn, footprints)
    n += insert_walkway_nodes(conn, walk["nodes"])
    n += insert_alternative_paths(conn, walk["alternative_paths"])
    n += insert_walkway_segments(conn, walk["segments"])
    n += insert_entrances(conn, entrances)
    # remove rows that were deleted from the JSON files (children first)
    _delete_missing(conn, "entrances", "entrance_id", [e["entrance_id"] for e in entrances])
    _delete_missing(conn, "walkway_segments", "segment_id", [s["segment_id"] for s in walk["segments"]])
    _delete_missing(conn, "walkway_nodes", "node_id", [x["node_id"] for x in walk["nodes"]])
    _delete_missing(conn, "alternative_paths", "path_id", [p["path_id"] for p in walk["alternative_paths"]])
    _delete_missing(conn, "building_footprints", "footprint_id", [f["footprint_id"] for f in footprints])
    return n, check


# ---------------------------------------------------------------- initialisation
def initialize_database(db_path=DEFAULT_DB_PATH, base_dir=BASE_DIR, force=False):
    """Create/upgrade the database and import source files that changed.

    Returns a status dict that the website shows on the Data page.
    """
    db_path = Path(db_path)
    base_dir = Path(base_dir)
    status = {"db_path": str(db_path), "created": not db_path.exists(), "recovered_from": None,
              "imports": [], "errors": []}

    problem = check_integrity(db_path)
    if problem:
        backup = db_path.with_name(f"{db_path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
        db_path.rename(backup)
        log.warning("Database was unreadable (%s); moved it to %s and rebuilt it", problem, backup.name)
        status["recovered_from"] = backup.name
        status["created"] = True

    conn = connect(db_path)
    try:
        if create_tables(conn):
            force = True                  # upgraded schema -> refresh every table from the source files
            status["migrated_to"] = SCHEMA_VERSION
        map_files = [SOURCE_FILES[k] for k in ("locations", "footprints", "walkways", "entrances")]
        for name in map_files + [SOURCE_FILES["workbook"]]:
            if not (base_dir / name).exists():
                status["errors"].append(f"Source file missing: {name}")
        if status["errors"]:
            have = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
            if not have:
                raise DatabaseError("; ".join(status["errors"]))
            return status   # keep running on the data already stored in SQLite

        # 1) map registries (imported together because they reference each other)
        hashes = {n: file_sha256(base_dir / n) for n in map_files}
        combined = hashlib.sha256("".join(hashes[n] for n in map_files).encode()).hexdigest()
        tag = "+".join(map_files)
        if force or not already_imported(conn, tag, combined):
            try:
                with transaction(conn):
                    n, check = import_map_registries(conn, base_dir)
                    record_import(conn, tag, combined, "success", n,
                                  f"{check['nodes']} nodes, {check['segments']} segments, all collision-checked")
                status["imports"].append({"source": tag, "status": "success", "records": n})
            except Exception as exc:
                record_import(conn, tag, combined, "failed", 0, str(exc))
                conn.commit()
                status["errors"].append(str(exc))
                raise
        else:
            status["imports"].append({"source": tag, "status": "unchanged"})

        # 2) Excel workbook
        wb_name = SOURCE_FILES["workbook"]
        wb_hash = file_sha256(base_dir / wb_name)
        if force or not already_imported(conn, wb_name, wb_hash):
            import data_loader
            try:
                with transaction(conn):
                    n, report = data_loader.import_workbook_to_db(conn, base_dir / wb_name)
                    note = (f"{report['counts']['pairs']} pairs, {report['counts']['main_measured']} main + "
                            f"{report['counts']['alternatives_measured']} alternative distances, "
                            f"{len(report['flagged'])} flagged")
                    record_import(conn, wb_name, wb_hash, "success", n, note)
                status["imports"].append({"source": wb_name, "status": "success", "records": n})
            except Exception as exc:
                record_import(conn, wb_name, wb_hash, "failed", 0, str(exc))
                conn.commit()
                status["errors"].append(str(exc))
                raise
        else:
            status["imports"].append({"source": wb_name, "status": "unchanged"})
    finally:
        conn.close()
    return status


# ---------------------------------------------------------------- queries used by the API / graph
def _row_with_json(row, *json_cols):
    d = dict(row)
    for c in json_cols:
        if d.get(c) is not None:
            d[c] = json.loads(d[c])
    return d


def get_buildings(conn):
    rows = conn.execute(
        """SELECT b.*, (SELECT COUNT(*) FROM entrances e WHERE e.building_id = b.building_id) AS entrance_count
           FROM buildings b ORDER BY b.legend_number""").fetchall()
    return [_row_with_json(r, "aliases") for r in rows]


def get_building(conn, building_id):
    row = conn.execute("SELECT * FROM buildings WHERE building_id = ?", (building_id,)).fetchone()
    return _row_with_json(row, "aliases") if row else None


def get_footprints(conn):
    return [_row_with_json(r, "footprint_coordinates")
            for r in conn.execute("SELECT * FROM building_footprints ORDER BY footprint_id")]


def get_entrances(conn, building_id=None):
    sql = """SELECT e.*, b.building_name FROM entrances e JOIN buildings b ON b.building_id = e.building_id"""
    if building_id:
        return [dict(r) for r in conn.execute(sql + " WHERE e.building_id = ? ORDER BY e.entrance_id", (building_id,))]
    return [dict(r) for r in conn.execute(sql + " ORDER BY b.legend_number, e.entrance_id")]


def get_walkway_nodes(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM walkway_nodes ORDER BY node_id")]


def get_walkway_segments(conn, walkable_only=True):
    sql = "SELECT * FROM walkway_segments"
    if walkable_only:
        sql += " WHERE is_walkable = 1"
    return [_row_with_json(r, "geometry_coordinates") for r in conn.execute(sql + " ORDER BY segment_id")]


def get_alternative_paths(conn):
    return [_row_with_json(r, "path_coordinates") for r in conn.execute("SELECT * FROM alternative_paths ORDER BY path_id")]


def get_walking_routes(conn, start_id=None, end_id=None):
    sql = """SELECT r.*, a.building_name AS start_name, b.building_name AS end_name
             FROM walking_routes r
             JOIN buildings a ON a.building_id = r.start_building_id
             JOIN buildings b ON b.building_id = r.end_building_id"""
    params = ()
    if start_id and end_id:
        sql += """ WHERE (r.start_building_id = ? AND r.end_building_id = ?)
                      OR (r.start_building_id = ? AND r.end_building_id = ?)"""
        params = (start_id, end_id, end_id, start_id)
    return [dict(r) for r in conn.execute(sql + " ORDER BY r.source_row, r.route_type", params)]


def save_route_history(conn, start_id, destination_id, total_distance_m, route_result):
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO route_history (start_location_id, destination_location_id, total_distance_m, route_result, search_timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (start_id, destination_id, total_distance_m, json.dumps(route_result, ensure_ascii=False), utc_now()))
    return cur.lastrowid


def get_route_history(conn, limit=20):
    rows = conn.execute(
        """SELECT h.*, a.building_name AS start_name, b.building_name AS destination_name
           FROM route_history h
           JOIN buildings a ON a.building_id = h.start_location_id
           JOIN buildings b ON b.building_id = h.destination_location_id
           ORDER BY h.history_id DESC LIMIT ?""", (int(limit),)).fetchall()
    return [_row_with_json(r, "route_result") for r in rows]


def clear_route_history(conn):
    with transaction(conn):
        n = conn.execute("DELETE FROM route_history").rowcount
    return n


def backup_database(src_path, dst_path):
    """Consistent copy of a live database using SQLite's online backup API
    (safe while the website is running)."""
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    src = connect(src_path)
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dst_path


def table_counts(conn):
    tables = ["buildings", "building_footprints", "entrances", "walkway_nodes", "walkway_segments",
              "walking_routes", "alternative_paths", "route_history", "data_imports"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) == 3 and sys.argv[1] == "--backup":
        print("backup written to", backup_database(configured_db_path(), sys.argv[2]))
        sys.exit(0)
    st = initialize_database(configured_db_path())
    print(json.dumps(st, indent=2))
    c = connect(configured_db_path())
    print(json.dumps(table_counts(c), indent=2))
    c.close()
