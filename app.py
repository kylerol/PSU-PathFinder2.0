"""
app.py
------
Flask web server for PSU PathFinder.

    python app.py                      ->  http://127.0.0.1:5000   (your computer / VS Code)
    gunicorn --bind 0.0.0.0:$PORT app:app ->  production server on Render

The browser never touches SQLite and never calculates a distance or a route
itself: every number and every line on the map comes from these endpoints.

    GET  /                       the website
    GET  /api/locations          14 official locations (+ map size)
    GET  /api/entrances          possible entrances (?building=<id>)
    GET  /api/paths              orange alternative paths + measured Excel alternatives
    GET  /api/walkways           walkway nodes + segments (traced geometry)
    GET  /api/footprints         building footprints (restricted areas)
    GET  /api/route-options      measured options for a pair (?start=&destination=) or all pairs
    POST /api/route              Dijkstra shortest route  {"start", "destination", "exclude_flagged"}
    GET  /api/history            saved successful searches (?limit=)
    DELETE /api/history          clear the saved searches
    POST /api/refresh-data       re-import the Excel workbook / JSON registries (ADMIN, no duplicates)
    GET  /api/data-report        validation report (workbook notes, flagged values, table counts)
    GET  /api/admin/backup       download a consistent copy of the SQLite database (ADMIN)
    GET  /healthz                health check used by the cloud host

Configuration comes from environment variables (see .env.example):
    DATABASE_PATH   where the SQLite file lives (Render: on the persistent disk, /var/data/psu_pathfinder.db)
    SECRET_KEY      Flask secret key
    ADMIN_TOKEN     required in the X-Admin-Token header for ADMIN endpoints
    FLASK_DEBUG     1 only on your own computer
    TRUST_PROXY     1 behind Render's HTTPS proxy
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, after_this_request, g, jsonify, render_template, request, send_file

import data_loader
import database
import graph
import map_processor

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("psu_pathfinder")

# Proponents as listed in the approved title proposal (Group 3).
PROPONENTS = (
    "Matthew David Q. Caoile",
    "Frederick Michael D. Cabanilla",
    "Keizer Alexus G. Paltep",
    "Frankie Mae M. Damian",
    "Jholan Froid Andrei P. Ramirez",
)
# The five portraits from page 3 ("Brief Profile of Proponents") of the proposal PDF, extracted
# unchanged and numbered in the order they appear on that page. They are shown as a gallery;
# the roster above is listed separately and no photo is labelled with a name.
PROPONENT_PHOTOS = tuple(f"images/proponent_photos/proponent_photo_{i}.jpg" for i in range(1, 6))


def create_app(db_path=None, base_dir=None, testing=False):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        DB_PATH=str(db_path or database.configured_db_path()),
        DATA_DIR=str(base_dir or BASE_DIR),
        TESTING=testing,
        JSON_SORT_KEYS=False,
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        ADMIN_TOKEN=os.environ.get("ADMIN_TOKEN", ""),
        DEBUG=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
    if os.environ.get("TRUST_PROXY", "0") == "1":
        # the cloud host terminates HTTPS and forwards requests; trust ONE proxy hop
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.json.sort_keys = False
    app.json.ensure_ascii = False

    # Refuse to silently lose data: warn loudly if the cloud database is not on a volume.
    storage = database.storage_status(app.config["DB_PATH"])
    app.config["STORAGE"] = storage
    if not storage["persistent"]:
        log.warning("DATABASE_PATH %s is NOT on a persistent disk - route history will be lost on redeploy. "
                    "Attach a Render persistent disk (mount path /var/data) and set "
                    "DATABASE_PATH=/var/data/psu_pathfinder.db.", storage["path"])

    # Create / upgrade the database and import only what changed.
    app.config["INIT_STATUS"] = database.initialize_database(app.config["DB_PATH"], app.config["DATA_DIR"])

    # ------------------------------------------------------------ helpers
    def get_db():
        if "db" not in g:
            g.db = database.connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def error(code, message, status=400):
        return jsonify({"success": False, "error": code, "message": message}), status

    def admin_denied():
        """ADMIN endpoints: with ADMIN_TOKEN set, the X-Admin-Token header must match it.
        Without ADMIN_TOKEN they only work from the computer running the server (127.0.0.1)."""
        token = app.config["ADMIN_TOKEN"]
        if token:
            sent = request.headers.get("X-Admin-Token", "")
            if hmac.compare_digest(sent.encode(), token.encode()):
                return None
            return error("ADMIN_ONLY", "This action needs the admin token.", 403)
        if request.remote_addr in ("127.0.0.1", "::1"):
            return None
        return error("ADMIN_ONLY", "This action is only allowed on the server itself or with ADMIN_TOKEN configured.", 403)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    @app.errorhandler(database.DatabaseError)
    def handle_db_error(exc):
        log.exception("database error")
        return error("DATABASE_ERROR", str(exc), 500)

    @app.errorhandler(sqlite3.Error)
    def handle_sqlite_error(exc):
        log.exception("sqlite error")
        return error("DATABASE_ERROR", f"The database could not complete the request: {exc}", 500)

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return error("NOT_FOUND", f"No API endpoint at {request.path}", 404)
        return _exc

    @app.errorhandler(405)
    def wrong_method(_exc):
        return error("METHOD_NOT_ALLOWED", f"{request.method} is not allowed on {request.path}", 405)

    # ------------------------------------------------------------ pages
    @app.get("/")
    def index():
        # The public address is only shown once the site really runs on Render:
        # Render sets RENDER_EXTERNAL_URL itself; PUBLIC_SITE_URL can override it (e.g. a custom domain).
        public_url = os.environ.get("PUBLIC_SITE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
        if not public_url.startswith("https://"):
            public_url = ""
        return render_template("index.html", public_url=public_url, proponent_photos=PROPONENT_PHOTOS,
                               proponents=PROPONENTS)

    # ------------------------------------------------------------ API: map data
    @app.get("/api/locations")
    def api_locations():
        locs = database.get_buildings(get_db())
        return jsonify({"success": True, "count": len(locs),
                        "map": {"image": "/static/images/campus_map.png",
                                "annotated_image": "/static/images/annotated_campus_map.png",
                                "width_px": map_processor.MAP_WIDTH_PX, "height_px": map_processor.MAP_HEIGHT_PX},
                        "locations": locs})

    @app.get("/api/entrances")
    def api_entrances():
        building = request.args.get("building")
        conn = get_db()
        if building and not database.get_building(conn, building):
            return error("INVALID_BUILDING", f"Unknown building '{building}'.", 404)
        ents = database.get_entrances(conn, building)
        return jsonify({"success": True, "count": len(ents), "entrances": ents})

    @app.get("/api/paths")
    def api_paths():
        conn = get_db()
        measured = [r for r in database.get_walking_routes(conn) if r["route_type"] != "main"]
        return jsonify({"success": True,
                        "annotated_paths": database.get_alternative_paths(conn),
                        "measured_alternatives": measured,
                        "note": "Annotated (orange) paths have no measured distance; they are drawn, and used for "
                                "route LINES on alternative legs, but never as Dijkstra distance weights."})

    @app.get("/api/walkways")
    def api_walkways():
        conn = get_db()
        return jsonify({"success": True, "nodes": database.get_walkway_nodes(conn),
                        "segments": database.get_walkway_segments(conn, walkable_only=False)})

    @app.get("/api/footprints")
    def api_footprints():
        fps = database.get_footprints(get_db())
        return jsonify({"success": True, "count": len(fps), "footprints": fps})

    @app.get("/api/route-options")
    def api_route_options():
        start, dest = request.args.get("start"), request.args.get("destination")
        conn = get_db()
        if start or dest:
            if not (start and dest):
                return error("BAD_REQUEST", "Give both start and destination, or neither.")
            for value, code in ((start, "INVALID_START"), (dest, "INVALID_DESTINATION")):
                if not database.get_building(conn, value):
                    return error(code, f"Unknown location '{value}'.", 404)
        rows = database.get_walking_routes(conn, start, dest)
        return jsonify({"success": True, "count": len(rows), "options": rows})

    # ------------------------------------------------------------ API: routing
    @app.post("/api/route")
    def api_route():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return error("BAD_REQUEST", "Send JSON like {\"start\": \"eng_bldg_1\", \"destination\": \"ptbi\"}.")
        start = str(body.get("start") or "").strip()
        dest = str(body.get("destination") or "").strip()
        exclude_flagged = bool(body.get("exclude_flagged", False))
        save = body.get("save_history", True) is not False
        conn = get_db()
        try:
            result = graph.find_route(conn, start, dest, exclude_flagged)
        except graph.RoutingError as exc:
            return error(exc.code, exc.message, exc.status)
        if save:
            summary = {"route": [r["building_id"] for r in result["route"]], "distance": result["distance"],
                       "hops": result["hops"], "exclude_flagged": exclude_flagged,
                       "legs": [{"from": l["from"], "to": l["to"], "distance": l["distance"],
                                 "option": l["option_label"], "description": l["description"]} for l in result["legs"]],
                       "geometry_status": result["geometry_status"]}
            result["history_id"] = database.save_route_history(conn, start, dest, result["distance"], summary)
        return jsonify(result)

    @app.get("/api/history")
    def api_history():
        try:
            limit = max(1, min(100, int(request.args.get("limit", 20))))
        except ValueError:
            return error("BAD_REQUEST", "limit must be a number")
        rows = database.get_route_history(get_db(), limit)
        return jsonify({"success": True, "count": len(rows), "history": rows})

    @app.delete("/api/history")
    def api_history_clear():
        denied = admin_denied()
        if denied:
            return denied
        n = database.clear_route_history(get_db())
        return jsonify({"success": True, "deleted": n})

    # ------------------------------------------------------------ API: data management
    @app.post("/api/refresh-data")
    def api_refresh():
        denied = admin_denied()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        try:
            status = database.initialize_database(app.config["DB_PATH"], app.config["DATA_DIR"],
                                                  force=bool(body.get("force", False)))
        except (database.DatabaseError, data_loader.DataError) as exc:
            return error("IMPORT_FAILED", str(exc), 500)
        app.config["INIT_STATUS"] = status
        counts = database.table_counts(get_db())
        return jsonify({"success": True, "status": status, "table_counts": counts})

    @app.get("/api/admin/backup")
    def api_backup():
        denied = admin_denied()
        if denied:
            return denied
        tmp = Path(tempfile.mkdtemp()) / f"psu_pathfinder-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
        database.backup_database(app.config["DB_PATH"], tmp)

        @after_this_request
        def cleanup(resp):
            try:
                tmp.unlink()
                tmp.parent.rmdir()
            except OSError:
                pass
            return resp

        return send_file(tmp, as_attachment=True, download_name=tmp.name, mimetype="application/vnd.sqlite3")

    @app.get("/healthz")
    def healthz():
        try:
            counts = database.table_counts(get_db())
        except Exception as exc:          # report, don't crash the health check
            return jsonify({"status": "error", "message": str(exc)}), 503
        ok = counts["buildings"] == 14 and counts["walking_routes"] > 0
        st = app.config["STORAGE"]
        return jsonify({"status": "ok" if ok else "degraded", "database": "connected",
                        "persistent_storage": st["persistent"],
                        "buildings": counts["buildings"], "walking_routes": counts["walking_routes"],
                        "route_history": counts["route_history"]}), (200 if ok else 503)

    @app.get("/api/data-report")
    def api_data_report():
        conn = get_db()
        try:
            result = data_loader.load_pairs(Path(app.config["DATA_DIR"]) / database.SOURCE_FILES["workbook"])
            report = data_loader.validate(result)
        except data_loader.DataError as exc:
            report = {"ok": False, "problems": [{"row": None, "problem": str(exc)}], "notes": [], "flagged": []}
        g_full = graph.build_building_graph(conn)
        fw = graph.floyd_warshall(g_full)
        shortcuts = 0
        pairs_best = {}
        for e_from, edges in g_full.adjacency.items():
            for e in edges:
                pairs_best[frozenset((e_from, e.target))] = e.weight
        for key, w in pairs_best.items():
            a, b = tuple(key)
            if fw[a][b] + 1e-9 < w:
                shortcuts += 1
        return jsonify({"success": True, "workbook": report, "table_counts": database.table_counts(conn),
                        "imports": database.last_imports(conn), "startup": app.config.get("INIT_STATUS"),
                        "graph": {"vertices": len(g_full.vertices()), "edges": g_full.edge_count() // 2,
                                  "pairs_with_shorter_multi_stop_route": shortcuts},
                        "storage": {"database_file": Path(app.config["DB_PATH"]).name,
                                    "persistent": app.config["STORAGE"]["persistent"],
                                    "cloud": app.config["STORAGE"]["cloud"]}})

    return app


def __getattr__(name):
    """`gunicorn app:app` (Render's start command) asks this module for `app`.
    The application is created on that first request for the attribute, so simply
    importing app.py (for example in the tests) never opens the production database."""
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module 'app' has no attribute {name!r}")


def lan_ip_address():
    """Best guess of this computer's address on the local Wi-Fi (no traffic is sent)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the PSU PathFinder website.")
    parser.add_argument("--lan", action="store_true",
                        help="also allow phones on the same Wi-Fi to open the site (listens on 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    application = create_app()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"\n  Database: {application.config['DB_PATH']}")
    print(f"\n  PSU PathFinder is running.  On this computer open  http://127.0.0.1:{args.port}")
    if args.lan:
        ip = lan_ip_address()
        if ip and not ip.startswith("127."):
            print(f"  On a phone connected to the SAME Wi-Fi open      http://{ip}:{args.port}")
        else:
            print("  Could not detect this computer's Wi-Fi address. Run 'ipconfig' and use the IPv4 Address.")
        print("  (If Windows Firewall asks, allow Python on Private networks.)")
    else:
        print("  To open it on a phone, stop the server and run:   python app.py --lan")
    print("  Press Ctrl+C to stop.\n")
    # Flask's built-in server is for your own computer only. Render runs gunicorn (see render.yaml / Procfile).
    application.run(host=host, port=args.port, debug=application.config["DEBUG"])
