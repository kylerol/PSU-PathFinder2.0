"""Cloud deployment: configuration, persistent database path, health check, admin protection."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import ROOT
import database
from app import create_app


class ConfigFileTests(unittest.TestCase):
    def test_files_present(self):
        for name in ("Procfile", "render.yaml", "wsgi.py", ".env.example", ".gitignore", "requirements.txt",
                     ".python-version", ".vscode/launch.json", ".vscode/settings.json"):
            self.assertTrue((ROOT / name).exists(), name)
        self.assertFalse((ROOT / "railway.json").exists())          # Render is the host

    def test_procfile_uses_gunicorn_on_port(self):
        proc = (ROOT / "Procfile").read_text()
        self.assertIn("gunicorn", proc)
        self.assertIn("0.0.0.0:$PORT", proc)
        self.assertIn("app:app", proc)
        self.assertIn("gunicorn", (ROOT / "requirements.txt").read_text())

    def test_render_blueprint(self):
        y = (ROOT / "render.yaml").read_text()
        for needle in ("type: web", "runtime: python", "buildCommand: pip install -r requirements.txt",
                       "gunicorn --bind 0.0.0.0:$PORT", "app:app", "healthCheckPath: /healthz",
                       "mountPath: /var/data", "numInstances: 1", "value: /var/data/psu_pathfinder.db",
                       "generateValue: true", "sync: false", 'value: "0"', "autoDeployTrigger: commit"):
            self.assertIn(needle, y)
        self.assertNotIn("plan: free", y)                             # free instances cannot attach a disk
        # no real secret values in the repository
        self.assertNotRegex(y, r"key: (SECRET_KEY|ADMIN_TOKEN)\n\s+value:")

    def test_app_module_exposes_app_for_gunicorn(self):
        import app as app_module
        self.assertNotIn("app", vars(app_module))                     # importing does not start the app
        self.assertTrue(callable(app_module.create_app))

    def test_secrets_and_database_not_committed(self):
        ignore = (ROOT / ".gitignore").read_text()
        for pattern in (".env", "instance/*.db"):
            self.assertIn(pattern, ignore)
        example = (ROOT / ".env.example").read_text()
        for var in ("DATABASE_PATH", "SECRET_KEY", "ADMIN_TOKEN", "FLASK_DEBUG"):
            self.assertIn(var, example)
        self.assertIn("/var/data/psu_pathfinder.db", example)


class DatabasePathTests(unittest.TestCase):
    def test_local_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(database.configured_db_path(), database.LOCAL_DB_PATH)

    def test_database_path_env(self):
        with mock.patch.dict(os.environ, {"DATABASE_PATH": "/var/data/psu_pathfinder.db"}, clear=True):
            self.assertEqual(database.configured_db_path(), Path("/var/data/psu_pathfinder.db"))

    def test_render_disk_detected_as_persistent(self):
        disk = Path(tempfile.mkdtemp())
        try:
            with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
                 mock.patch("database.os.path.ismount", side_effect=lambda p: Path(p) == disk):
                st = database.storage_status(disk / "psu_pathfinder.db")
                self.assertTrue(st["persistent"])
                self.assertEqual(st["volume_mount"], str(disk))
            with mock.patch.dict(os.environ, {"RENDER": "true"}, clear=True), \
                 mock.patch("database.os.path.ismount", return_value=False):
                self.assertFalse(database.storage_status(disk / "psu_pathfinder.db")["persistent"])   # no disk
        finally:
            shutil.rmtree(disk, ignore_errors=True)

    def test_laptop_is_persistent(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(database.storage_status(database.LOCAL_DB_PATH)["persistent"])


class ProductionAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "volume" / "psu_pathfinder.db"     # simulates the Render disk at /var/data

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, **env):
        with mock.patch.dict(os.environ, {"DATABASE_PATH": str(self.db), **env}):
            return create_app(testing=True)

    def test_database_created_on_volume_path(self):
        app = self.make()
        self.assertEqual(app.config["DB_PATH"], str(self.db))
        self.assertTrue(self.db.exists())
        self.assertFalse(app.config["DEBUG"])

    def test_healthz(self):
        r = self.make().test_client().get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")

    def test_history_survives_redeploy(self):
        """A redeploy = a new app process using the same volume file."""
        c1 = self.make().test_client()
        c1.post("/api/route", json={"start": "main_gate", "destination": "ptbi"})
        c2 = self.make().test_client()                    # "new deployment"
        hist = c2.get("/api/history").get_json()["history"]
        self.assertEqual(hist[0]["destination_location_id"], "ptbi")

    def test_admin_token_required(self):
        c = self.make(ADMIN_TOKEN="s3cret").test_client()
        self.assertEqual(c.delete("/api/history").status_code, 403)
        self.assertEqual(c.post("/api/refresh-data", json={}).status_code, 403)
        self.assertEqual(c.get("/api/admin/backup").status_code, 403)
        self.assertEqual(c.delete("/api/history", headers={"X-Admin-Token": "wrong"}).status_code, 403)
        self.assertEqual(c.delete("/api/history", headers={"X-Admin-Token": "s3cret"}).status_code, 200)

    def test_admin_blocked_for_public_without_token(self):
        app = self.make(ADMIN_TOKEN="")
        c = app.test_client()
        remote = {"REMOTE_ADDR": "203.0.113.7"}
        self.assertEqual(c.delete("/api/history", environ_base=remote).status_code, 403)
        self.assertEqual(c.post("/api/refresh-data", json={}, environ_base=remote).status_code, 403)
        # route search stays public
        self.assertEqual(c.post("/api/route", json={"start": "main_gate", "destination": "sac"}, environ_base=remote).status_code, 200)

    def test_backup_download_is_valid_sqlite(self):
        c = self.make(ADMIN_TOKEN="s3cret").test_client()
        r = c.get("/api/admin/backup", headers={"X-Admin-Token": "s3cret"})
        self.assertEqual(r.status_code, 200)
        out = self.tmp / "backup.db"
        out.write_bytes(r.data)
        r.close()
        con = sqlite3.connect(out)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0], 14)
        con.close()

    def test_security_headers(self):
        r = self.make().test_client().get("/")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()


class PublicUrlTests(unittest.TestCase):
    """The About page never invents a public address."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self, **env):
        with mock.patch.dict(os.environ, {"DATABASE_PATH": str(self.tmp / "p.db"), **env}, clear=True):
            return create_app(testing=True).test_client().get("/").get_data(as_text=True)

    def test_not_deployed_yet(self):
        html = self.page()
        self.assertIn("Online deployment is still in progress", html)
        self.assertNotIn("onrender.com", html)
        self.assertNotIn("PSU PathFinder is deployed through Render", html)

    def test_render_url_shown_when_running_on_render(self):
        html = self.page(RENDER_EXTERNAL_URL="https://example-service.onrender.com")
        self.assertIn('href="https://example-service.onrender.com"', html)
        self.assertIn("PSU PathFinder is deployed through Render", html)
        self.assertNotIn("Online deployment is still in progress", html)
