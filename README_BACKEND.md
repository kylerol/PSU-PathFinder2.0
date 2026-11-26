# PSU PathFinder – Backend package

This package is the Python/Flask/SQLite half of PSU PathFinder. Every file in it is identical to the
same file in `PSU_PathFinder_Complete_Project.zip`.

| File | Role |
|---|---|
| `app.py` | Flask app factory, API endpoints, `/healthz`, admin protection; `gunicorn app:app` entry point |
| `database.py` | SQLite schema (v4), migrations, duplicate-safe imports, queries, backup |
| `graph.py` | adjacency-list graph, Dijkstra with `heapq`, route geometry, building-collision retry |
| `data_loader.py` | Excel (`campus_walking_distances.xlsx`) parsing, validation, PBTI → PTBI normalisation |
| `map_processor.py`, `prepare_map.py` | map cropping/alignment, footprint collision checks, annotation audit |
| `locations.json`, `entrances.json`, `walkways.json`, `footprints.json` | initial campus data (imported into SQLite) |
| `source_images/` | the uploaded maps, untouched (input of `prepare_map.py`) |
| `requirements*.txt`, `Procfile`, `render.yaml`, `.python-version`, `.env.example`, `.gitignore` | setup and Render deployment |
| `tests/` | backend tests (data, database, routing, map geometry) |

## Running the backend with the website
The backend serves the website from `templates/` and `static/`, which are in the **frontend package**.
Unzip both packages **into the same folder** (or use the complete project), then:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Without the frontend files the API still works (for example `http://127.0.0.1:5000/api/locations`),
but `/` returns an error because `templates/index.html` is missing.

Tests in this package: `python -m pytest tests -q` (`pip install -r requirements-dev.txt` first).
The page and deployment tests (`test_api.py`, `test_deployment.py`) need the frontend files too,
so they are only in the complete project.

No secrets and no database file are included. The database is created on first run at
`instance/psu_pathfinder.db` (locally) or at `DATABASE_PATH` (Render: `/var/data/psu_pathfinder.db`).
See `README.md` for the full guide, including GitHub and Render deployment.
