# PSU PathFinder
### A Campus Navigation and Shortest-Path Optimization System
Pangasinan State University – Urdaneta Campus · Data Structures and Algorithms final project (Group 3)

Proponents: Matthew David Q. Caoile · Frederick Michael D. Cabanilla · Keizer Alexus G. Paltep · Frankie Mae M. Damian · Jholan Froid Andrei P. Ramirez

PSU PathFinder is a **desktop-first, responsive website** built with Python, Flask and SQLite. It shows the actual campus map with:
- the 14 official locations (building 8 is **PTBI**; the older spelling "PBTI" is only a search alias),
- the red Main Gate,
- 17 possible building entrances (blue dots),
- **original campus walkways (purple)** and **alternative walking paths (orange)**, including the new
  **Quadrangle alternative path** (`alt_quadrangle_01`) around Engineering Bldg. 1.

It finds the shortest **measured** walking route between two locations with a hand-written **Dijkstra's algorithm**
(a `heapq` min-heap on an adjacency-list graph). The route line is drawn **along modelled campus walkways** and is
checked so it never passes through a building.

It uses no GPS, no hardware, no Google Maps or OpenStreetMap, no CDN and no cloud database. It runs on your computer
and is prepared for **GitHub + Render** hosting with SQLite on a Render persistent disk.

---

## 1. Run it on your own computer (Windows)

1. **Install Python 3.12** (3.9 or newer works) from <https://www.python.org/downloads/>. During setup tick
   **"Add python.exe to PATH"**.
2. Open **Command Prompt** in the project folder:
   ```bat
   cd C:\path\to\PSU_PathFinder
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
   In PowerShell activate with `.venv\Scripts\Activate.ps1`. If PowerShell blocks the script, run
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
3. Open **http://127.0.0.1:5000**.
4. Stop with `Ctrl + C`; start again with `python app.py`. The database (`instance\psu_pathfinder.db`) and the route
   history are kept.

`gunicorn` (the production server) does not run on Windows, so pip skips it there. Locally, `python app.py` is all you need.

### What happens on the first run
1. `instance/psu_pathfinder.db` and all of its tables are created.
2. `locations.json`, `footprints.json`, `walkways.json` and `entrances.json` are validated and imported. The import is
   refused if any **walkable** walkway passes through a building.
3. `campus_walking_distances.xlsx` is parsed, validated and imported into `walking_routes` (the workbook is opened
   read-only and never changed).

Each import is recorded in `data_imports` with the file's SHA-256 hash. On later starts a file is re-imported **only if
it changed**, so nothing is duplicated and the route history is never deleted. An older database (for example one that
still uses the ID `pbti`) is migrated in place to schema v4 and keeps its history.

### On a phone on the same Wi-Fi (no internet needed)
Run `python app.py --lan` and open the address it prints (for example `http://192.168.1.23:5000`) on the phone. If
Windows Firewall asks, allow Python on Private networks.

### Useful commands
| Command | What it does |
|---|---|
| `python -m pytest tests -q` (or `python -m unittest discover -s tests -v`) | Runs all **126 tests** |
| `python data_loader.py` | Excel validation report |
| `python database.py` | Creates or updates the database and prints the table counts |
| `python database.py --backup backups\copy.db` | Makes a safe copy of the database |
| `python prepare_map.py --check` | Checks every walkway against every building footprint |
| `python prepare_map.py` | Rebuilds `static/images/*.png` from `source_images/` (needs `pip install -r requirements-tools.txt`) |
| `python prepare_map.py --audit` | Re-detects the red, blue, purple and orange marks on the latest map and compares them with the JSON files |

---

## 1A. Editing the project in Visual Studio Code

The folder is ready to open in VS Code (**File → Open Folder… → PSU_PathFinder**).

1. Install the recommended extensions when VS Code asks (`.vscode/extensions.json`): Python, Python Debugger, Pylance
   and SQLite Viewer (to look inside `instance/psu_pathfinder.db`).
2. Create the virtual environment once (section 1, step 2) in the **Terminal → New Terminal** panel, then also run
   `pip install -r requirements-dev.txt` for pytest. `.vscode/settings.json` already points VS Code at `.venv`.
3. **Run and Debug** (Ctrl + Shift + D) offers ready-made launch configurations:
   - *PSU PathFinder: run website* – starts `app.py`; set breakpoints in any `.py` file.
   - *run website for phones on the same Wi-Fi (--lan)*.
   - *Excel data report* and *all tests*.
4. **Testing** panel (flask icon) discovers the tests in `tests/` automatically.
5. **Terminal → Run Task…** has *Run tests*, *Audit map annotations* and *Back up the local database*.

Where to change things:

| You want to change… | Edit | Then |
|---|---|---|
| a building name, colour or position | `locations.json` | restart `python app.py` (re-imported automatically) |
| an entrance | `entrances.json` | restart |
| a walkway or a measured segment length (`distance_m`) | `walkways.json` | restart; the import is refused if a walkable line crosses a building |
| a walking distance | `campus_walking_distances.xlsx` (a copy – keep the original) | restart |
| the page layout or texts | `templates/index.html` | refresh the browser |
| colours and layout | `static/css/styles.css` (colours are variables in `:root`) | refresh |
| map behaviour | `static/js/app.js` | refresh |
| the algorithm | `graph.py` | restart, then run the tests |

`.editorconfig` keeps indentation consistent (4 spaces in Python, 2 in HTML/CSS/JS, UTF-8, LF line endings).

---

## 2. DEPLOYING PSU PATHFINDER ONLINE THROUGH GITHUB

The website runs on **Render** as a Python web service connected to your GitHub repository. Render builds it with
`pip install -r requirements.txt` and starts it with **gunicorn** (Flask's development server is never used online).
The SQLite database lives on a **Render persistent disk** mounted at `/var/data`, so the route history survives
restarts and redeployments, and the site keeps running while your laptop is off.

> **Nothing here has been deployed yet.** The GitHub repository and the Render service must be created with your own
> accounts. The About page shows "Online deployment is still in progress" until the site really runs on Render; then
> it shows the address Render assigns (Render provides it in the `RENDER_EXTERNAL_URL` variable). No URL is made up.

### 2.1 Costs – read this before you create anything (checked November 2026)
| Option | Cost | Persistent SQLite? | Always on? |
|---|---|---|---|
| **Render paid web service + persistent disk** (recommended) | smallest paid instance ("Starter", 0.5 CPU / 512 MB) **$7/month**, plus the disk at **$0.25 per GB per month** (1 GB is enough) – about **$7.25/month**, billed by Render, prorated by the second | **Yes** (`/var/data`) | Yes |
| Render **free** web service | $0 | **No** – free services cannot attach a disk, so the database is wiped on every deploy/restart and the route history is lost | **No** – spins down after 15 minutes without traffic; the next visit waits for it to start again (about a minute) |

Prices change; confirm them on <https://render.com/pricing> before choosing. A free Render service is fine for a quick
demo of the map and routing, but it cannot meet the "history survives redeployment" requirement, and free 24/7 hosting
is **not** offered. Render also takes an automatic daily snapshot of a paid disk and keeps it for seven days.

Limits that come with a disk (by Render's design, and correct for SQLite):
- the service must stay at **one instance** (`numInstances: 1` in `render.yaml`) – never scale it out;
- each deploy has a few seconds of downtime while the old instance stops and the new one starts;
- only files under `/var/data` are kept; everything else in the container is reset on each deploy.

### 2.2 Files already prepared for deployment
| File | Purpose |
|---|---|
| `render.yaml` | Render Blueprint: Python web service, build/start commands, `/healthz` health check, 1 GB disk at `/var/data`, one instance, auto-deploy on every commit, environment variables |
| `requirements.txt` | Flask, openpyxl, gunicorn |
| `Procfile` | the same gunicorn command (for hosts that read a Procfile) |
| `.python-version` | `3.12` (Render uses the latest 3.12.x) |
| `app.py` | exposes `app` for `gunicorn app:app`; `wsgi.py` is an equivalent entry point |
| `.env.example` | the variable **names** (no real values) |
| `.gitignore` | keeps `.env`, `.venv`, `instance/*.db` and backups out of Git |

Start command (from `render.yaml`):
```
gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60 --preload --access-logfile - app:app
```

### 2.3 Create the GitHub repository and push (Windows Command Prompt)
1. Install Git from <https://git-scm.com/download/win> and sign in to <https://github.com>.
2. On GitHub click **New repository**, name it `PSU-PathFinder`, leave "Add a README" **unticked**, create it.
3. In the project folder:
   ```bat
   git init
   git branch -M main
   git add .
   git status
   ```
   Check the `git status` list: there must be **no** `.env`, `.venv\`, or `instance\*.db` files.
   ```bat
   git commit -m "PSU PathFinder: campus navigation website"
   git remote add origin https://github.com/YOUR_GITHUB_USERNAME/PSU-PathFinder.git
   git push -u origin main
   ```
   Replace `YOUR_GITHUB_USERNAME` with your own username (copy the exact URL GitHub shows you).

### 2.4 Create the Render web service
**Option A – Blueprint (uses `render.yaml`, recommended)**
1. Sign in at <https://dashboard.render.com> with GitHub and allow Render to read the `PSU-PathFinder` repository.
2. Click **New → Blueprint**, choose the repository and branch `main`.
3. Render reads `render.yaml` and shows the service `psu-pathfinder` with its disk. It asks for **ADMIN_TOKEN**: type a
   long private password (for example the output of `python -c "import secrets; print(secrets.token_urlsafe(24))"`).
   Keep it somewhere safe – it is never stored in Git.
4. Review the plan and cost shown by Render, then click **Apply / Deploy**.

**Option B – by hand**
1. **New → Web Service**, connect the repository, branch `main`, runtime **Python 3**.
2. Build command `pip install -r requirements.txt`; start command as in section 2.2.
3. Instance type: a **paid** instance (Starter). Under **Advanced → Add Disk**: name `psu-pathfinder-data`,
   mount path `/var/data`, size 1 GB.
4. Health check path: `/healthz`.
5. Environment variables:

| Key | Value |
|---|---|
| `DATABASE_PATH` | `/var/data/psu_pathfinder.db` |
| `SECRET_KEY` | a long random string (*Generate* in Render) |
| `ADMIN_TOKEN` | your private admin password |
| `FLASK_DEBUG` | `0` |
| `TRUST_PROXY` | `1` |

Do not set `PORT`; Render sets it.

### 2.5 Find the public URL
When the deploy log shows `Booting worker` and the service status is **Live**, the URL is at the top of the service
page, in the form `https://<service-name>.onrender.com` (the exact name is chosen by Render). Open it; the About page
now shows this address. Put the same URL in the final paper where it says `[INSERT ACTUAL RENDER WEBSITE URL]`.

### 2.6 Automatic deployment after changes
`autoDeployTrigger: commit` makes Render redeploy on every push to `main`:
```bat
git add .
git commit -m "Describe the change"
git push
```
The disk at `/var/data` is not touched by a deploy. On start-up the app re-imports only source files whose content
changed, so history and data are kept and nothing is duplicated.

### 2.7 Check that it works without your laptop
1. Shut down your laptop (or stop `python app.py`).
2. On a phone using **mobile data** (not your Wi-Fi), open the Render URL.
3. Open `https://<your-url>/healthz`. Expect `"status": "ok"` and **`"persistent_storage": true`**. If it says
   `false`, the disk is missing or `DATABASE_PATH` is not under `/var/data`.
4. Find a route, then in Render click **Manual Deploy → Deploy latest commit**. After it is Live again, the search is
   still under **Route History**.

### 2.8 Admin actions and security
- Clearing history, re-importing data and downloading a backup need the `X-Admin-Token` header (compared in constant
  time). Without `ADMIN_TOKEN` they are allowed only from the same computer (127.0.0.1). The public can only view the
  map, find routes and read the history.
- The browser never sees the SQLite file; every database operation goes through Flask.
- `FLASK_DEBUG=0` in production; security headers (CSP without external sources, `X-Frame-Options`, `nosniff`) are
  added to every response.
- Never commit real secret keys, tokens, hosting credentials or runtime database files.

### 2.9 Backing up and restoring the online database
**Backup** (Windows, `curl.exe` is built into Windows 10/11):
```bat
mkdir backups
curl.exe -H "X-Admin-Token: YOUR_ADMIN_TOKEN" -o backups\psu_pathfinder-backup.db https://YOUR-SERVICE.onrender.com/api/admin/backup
```
The file is made with SQLite's online backup API, so it is consistent even while the site is in use. Open it with
SQLite Viewer in VS Code to check it. Render's daily disk snapshots are a second safety net.

**Restore:**
1. In Render open the service → **Shell** (available on paid instances).
2. Upload the backup somewhere you can download from (for example a private GitHub release asset), then in the shell:
   ```bash
   cp /var/data/psu_pathfinder.db /var/data/psu_pathfinder.before-restore.db
   curl -L -o /var/data/psu_pathfinder.db "<download link of your backup>"
   ```
3. **Manual Deploy → Restart service**. `/healthz` should report the restored history count.

Locally: `python database.py --backup backups\copy.db`; to restore, stop the app and copy the file back to
`instance\psu_pathfinder.db`.

---

## 3. How the pieces fit together

```
Excel workbook ──► data_loader.py ──┐
original + 3 annotated maps ──► prepare_map.py / map_processor.py ──► locations / footprints /
                                                       walkways / entrances .json ──► database.py ──► SQLite
                                                                                                     │
               graph.py: building graph (metres) + walkway graph (purple/original + orange/alternative)
                         Dijkstra (heapq) + building-collision check
                                                                                                     ▼
               app.py (Flask API, gunicorn on Render) ──► static/js/app.js ──► index.html + styles.css
```

| Data | Initial source | Runtime source |
|---|---|---|
| Building IDs, names, colours, x/y | `locations.json` | `buildings` |
| Building footprints (no-walk areas) | `footprints.json` | `building_footprints` |
| Possible entrances (blue dots) | `entrances.json` | `entrances` |
| Walkway nodes and polylines, purple and orange | `walkways.json` | `walkway_nodes`, `walkway_segments` |
| Orange alternative paths (grouped, incl. `alt_quadrangle_01`) | `walkways.json` | `alternative_paths` |
| Walking distances | Excel *Pairs* sheet | `walking_routes` |
| Previous searches | – | `route_history` |

The JavaScript holds **no** building names, IDs, coordinates or distances. A test fails if anyone hard-codes them.

---

## 4. Project structure
```
PSU_PathFinder/
├── app.py  wsgi.py  database.py  graph.py  data_loader.py  map_processor.py  prepare_map.py
├── locations.json  entrances.json  walkways.json  footprints.json
├── campus_walking_distances.xlsx        (opened read-only, never overwritten)
├── requirements.txt  requirements-dev.txt  requirements-tools.txt
├── render.yaml  Procfile  .python-version  .env.example  .gitignore  .editorconfig
├── .vscode/  settings.json  launch.json  tasks.json  extensions.json
├── instance/                            local database (not in Git); Render uses /var/data
├── source_images/                       the uploaded maps, untouched (read-only)
│     campus_map_original.png            image.png – original map with legend
│     campus_map_annotated.png           image(1).png – earlier annotated map
│     campus_map_revised_annotated.png   image(2).png – purple lines + new Eng'g Bldg. 2 doors
│     campus_map_latest_annotated.png    image(3).png – adds the Quadrangle path (authoritative)
├── templates/index.html
├── static/css/styles.css  static/js/app.js  static/manifest.json
├── static/images/campus_map.png                     original map, legend cropped off (944 × 1472)
├── static/images/annotated_campus_map.png           earlier annotated map, aligned
├── static/images/latest_annotated_campus_map.png    latest annotated map (image(3)), aligned
├── static/images/proponent_photos/                  the 5 portraits from the proposal, page 3
├── documentation/  PSU_PathFinder_Final_Project_Paper.docx / .pdf  progress_report.md
├── README_BACKEND.md  README_FRONTEND.md       what is in the separate backend / frontend downloads
└── tests/ test_data.py  test_database.py  test_routing.py  test_map_geometry.py  test_api.py  test_deployment.py
```

---

## 5. Maps, annotations and the walkway model

**Frame.** The original map panel is cropped to **944 × 1472 px** and is the reference for every coordinate (`x`, `y` = % from the left and top edges). Both annotated phone screenshots have their black bars cropped off and are registered to that frame with SIFT + RANSAC:
- earlier map: 345 matches, 0.80 px RMS error;
- latest maps (image(2) and image(3) are pixel-identical except for the new Quadrangle stroke): 379 matches, 0.81 px RMS error.

The map's **Latest / Earlier / Map** button swaps the background, so anyone can check the overlays against the hand-drawn marks.

**Classification follows the latest annotated map:**

| Mark | Model |
|---|---|
| Red dot | `main_gate` (one entry), with its walkway node on the dot |
| 17 blue dots | `entrances.json`: 15 from the earlier map (unchanged, ≤ 0.3 px), plus **2 new Eng'g Bldg. 2 entrances** (north-west and west, both facing the main road). 16 connect to walkways; the dot drawn *inside* Acad Bldg. 2 is display-only. |
| **Purple strokes** | **7 `original_walkway` segments** (`annotation_colour = purple`): Acad Bldg. 2 to the main road (new), west and south of Acad Bldg. 1, the inverted V south of the gate plaza, and the lane between New Eng'g and CAE |
| **Orange strokes** | **6 `alternative_walkway` segments** (`annotation_colour = orange`), grouped as 2 alternative paths: between Acad Bldg. 1 and 2, and behind Eng'g Bldg. 1 |
| **NEW orange Quadrangle stroke** (image(3)) | **`alt_quadrangle_01`**: 4 `alternative_walkway` segments (west porch stub, west + north corridor, west + south corridor past the back entrance, tail to the orange path behind the building). The stroke is drawn **over the Eng'g Bldg. 1 footprint** (the corridor ring around the courtyard), so it is stored with `verification_status = unverified`, `is_walkable = 0` and `distance_m = NULL`: it is shown dashed on the map but **excluded from Dijkstra** until the corridor is confirmed open to pedestrians and measured. |
| Re-marked strokes | Four strokes that were orange on the earlier map are drawn purple on the latest map. They are now **original** walkways, recorded under `walkways.json → other_annotations`. |
| Green dot | Its meaning is not defined. It is recorded, not used. |

The rest of the original network is **21 paved-road segments** traced from the base map: the main campus road, the north road, the road between Eng'g Bldg. 1 and PTBI, the south road and the east service road. On the website, every original walkway is purple: annotated strokes are solid and traced roads are dashed. Alternative paths are orange.

The model also has:
- **12 entrance connectors**, from each connected door to the walkway beside it;
- **20 footprints**: 13 legend buildings plus 7 plan structures;
- 52 nodes and 53 segments: 49 walkable segments, all connected to the Main Gate, plus the 4 display-only Quadrangle segments.

`distance_m` is **NULL for every segment**, because none has been measured on site.

`prepare_map.py --audit` results:
- the red dot is within 1.5 px of the Main Gate;
- all 17 blue dots are within 1.6 px of their entrances (the back door of Eng'g Bldg. 1 is partly painted over by the
  new orange stroke; it is confirmed at 1.2 px on image(2));
- **97.3 % of the purple pixels** lie on modelled original walkways;
- **98.5 % of the orange pixels** that are not under purple lie on modelled alternative walkways (including the Quadrangle path).

Result: **AUDIT PASSED**.

---

## 6. Excel processing
The *Pairs* sheet holds 14 locations and 91 unique pairs, with **91 main + 36 alternative distances**. Text such as `"61.99 m"` is parsed to a number, and blank or `N/A` cells mean *unavailable*, never 0. The shortest available option is selected for each pair. Values under 10 m are flagged and kept unchanged.

| Pair | Main | Alternative | Selected |
|---|---|---|---|
| Eng'g Bldg. 1 → Eng'g Bldg. 2 | 61.99 m | – | **61.99 m** |
| Eng'g Bldg. 1 → Acad Bldg. 1 | 148.27 m | 104.78 m | **104.78 m** |
| Eng'g Bldg. 1 → PTBI | 128.40 m | 70.71 m | **70.71 m** |
| CAE Bldg. → New Eng'g Bldg. | 49.77 m | 3 m ⚠ | **3 m, flagged** |

## 7. Conflicts found in the source files (none silently "fixed")
| # | Finding | Decision |
|---|---|---|
| 1 | *How to use* says all distances are blank, but *Pairs* has 127 values | *Pairs* is the source of truth |
| 2 | The distances are stored as text, so Excel's *Shortest* column and *Matrix* sheet stay blank | Python parses the text; blank matrix cells are never treated as 0 |
| 3 | CAE ↔ New Eng'g alternative = **3 m** | Flagged and kept; a tick-box ignores it (49.77 m) |
| 4 | Teacher's Ed ↔ DOST = **8.52 m** | Flagged; without it the route goes via the Canteen (48.24 m) |
| 5 | Teacher's Ed and DOST have identical distances to 3 places | Reported as a possible copied value |
| 6 | 21 pairs have a shorter multi-stop route than their direct measurement | Kept; Dijkstra finds them (Main Gate → ERB: 212.73 m via Eng'g Bldg. 2 instead of 253.04 m) |
| 7 | The plan calls the pink building "School of Advanced Studies"; the legend calls it CAE, and calls Acad Bldg. 2 "SAS" | The legend is followed; the plan label is stored |
| 8 | The plan says "PTBI"; the legend and Excel say "PBTI" | **PTBI is official** (ID `ptbi`, corrected by the proponents). "PBTI" is normalised to `ptbi` on import and kept only as a search alias; there is one record |
| 9 | The Amphitheater area is labelled "Student Center" on the plan | Stored as an alias |
| 10 | **Earlier map: four strokes orange; latest map: the same strokes purple** | The latest map wins: they are original walkways (recorded) |
| 11 | The legend colours Eng'g Bldg. 2 as one block that includes an annex | The annex door is `unverified` |
| 12 | The blue dot between New Eng'g and CAE touches both buildings | Assigned to CAE as `unverified` |
| 13 | A second "GATE" label on the highway side | A walkway end point only |
| 14 | The re-uploaded original map differs from the first upload by JPEG noise only (mean 1.2/255) | The first copy is kept; the content is identical |
| 15 | **image(3) draws the new orange Quadrangle path over the Eng'g Bldg. 1 footprint** | Stored and displayed as `alt_quadrangle_01`, **unverified and not routable**; distance NULL |

---

## 8. Graphs and Dijkstra (`graph.py`)
```python
while heap:
    d, u = heappop(heap)                       # nearest unfinished vertex (min-heap)
    if u in visited: continue                  # stale entry
    visited.add(u)
    if u in targets: break
    for edge in graph[u]:                      # adjacency list
        nd = d + edge.weight
        if nd < distances[edge.target]:        # relaxation
            distances[edge.target] = nd
            previous[edge.target] = (u, edge)
            heappush(heap, (nd, edge.target))
```
The same `dijkstra()` runs on two graphs:

| | Building graph | Walkway graph |
|---|---|---|
| Vertices | 14 locations | walkway nodes (junctions, doors, frontage, gate) |
| Edges | 91 measured pairs (`walking_routes`) | the 49 walkable modelled segments (`is_walkable = 1`) |
| Weight | shortest **measured** metres | drawn length in **map pixels**, never shown as metres |
| Gives | **distance and stop order** | **which walkway line to draw** |

How each leg's line is chosen:
- A **main-route** leg ("use the main road") may only use **original walkways**: paved roads, the purple strokes, door connectors and the gate road.
- An **alternative-route** leg may also use the orange paths.
- Every chosen segment is checked against every footprint. A failing segment is removed and the search runs again. If no valid line exists, none is drawn and the distance is still shown.
- The response always says `geometry_verified: false`, because a traced line is not a field survey.

Example: thanks to the new Eng'g Bldg. 2 doors and the purple walkway, **Eng'g Bldg. 2 → Acad Bldg. 2** now leaves by the north-west door and walks the purple walkway to Acad Bldg. 2's south-east door. **Eng'g Bldg. 2 → PTBI** ("pathway on the right of quad", 120.15 m) follows the orange path behind Eng'g Bldg. 1.

---

## 9. SQLite
The tables are `buildings`, `building_footprints`, `entrances`, `walkway_nodes`, `walkway_segments`, `walking_routes`, `alternative_paths`, `route_history`, `data_imports` and `schema_version` (now **v4**; older files are migrated in place).
- `walkway_segments.segment_type` is one of `original_walkway` (purple / traced roads), `alternative_walkway` (orange), `main_gate_access`, `verified_pedestrian_connection`, plus the connector types `entrance_access` and `junction_link`. `annotation_colour` records purple/orange; `is_walkable = 0` keeps a segment out of routing.
- Foreign keys are ON, with CHECK constraints and parameterised SQL. Imports run inside transactions and use upserts, so they never duplicate rows.
- The path comes from `DATABASE_PATH` (Render: `/var/data/psu_pathfinder.db`), otherwise `instance/psu_pathfinder.db`.
- A corrupted file is renamed and rebuilt. `/healthz` and the startup log warn if the database on Render is **not** on the persistent disk.

## 10. API
| Method & path | Returns |
|---|---|
| `GET /api/locations` · `/api/entrances[?building=]` · `/api/walkways` · `/api/paths` · `/api/footprints` | map data |
| `GET /api/route-options[?start=&destination=]` | measured options |
| `POST /api/route` | `{"start","destination","exclude_flagged","save_history"}` → distance, walking time, legs, directions, geometry |
| `GET /api/history` | saved searches |
| `DELETE /api/history` | **admin**: clear the history |
| `POST /api/refresh-data` | **admin**: safe re-import |
| `GET /api/admin/backup` | **admin**: download the database |
| `GET /api/data-report` | validation report and storage status |
| `GET /healthz` | health check for the host |

## 11. Using the website
- **Desktop:** the planner sits in a side panel next to a large map. Search by name or alias. Click a building, entrance, purple walkway or orange path (including the dashed Quadrangle path) for its details. *Find Shortest Route* shows the distance, an approximate walking time (80 m/min), the stops, every measured option, turn-by-turn directions and *How Dijkstra found it*.
- **Phone:** a full-screen map works like Google Maps, with a floating search bar, a draggable bottom sheet (peek / half / full), pinch and double-tap zoom, and the Back button to close it. Use *Add to Home Screen* for an app-like icon.
- **Legend toggles** for every layer, plus **Latest / Earlier / Map** to switch the background image.
- **About Project** explains the purpose, objectives, technologies and how routing works; **Our Team** shows the five proposal portraits as a gallery with the roster listed separately. The former user-facing "data notes" panel was removed; the validation report stays available at `GET /api/data-report`.

## 12. Testing
`python -m pytest tests -q` → **126 tests, all passing** (run on 26 November 2026 in the development environment).

| File | Covers |
|---|---|
| `test_data.py` | Excel parsing and validation, 91 pairs, N/A handling, flagged values, **PBTI → `ptbi` normalisation (one record)**, the canonical registry, no hard-coded front-end data |
| `test_database.py` | Schema, FK enforcement, imports without duplicates, persistence, corruption recovery, a walkway through a building refused at import |
| `test_routing.py` | Dijkstra on small graphs (direct, multi-edge, unreachable, invalid, same place), the campus samples, equal to Floyd–Warshall for all 182 ordered pairs, the flagged toggle |
| `test_map_geometry.py` | Images aligned; no walkable segment crosses a building; a straight centre-to-centre line *is* rejected; all 182 routes follow walkways door to door; purple = original, orange = alternative, never mixed; **Quadrangle path stored, unverified, NULL distance, excluded from Dijkstra** |
| `test_api.py` | every endpoint and error code, the JSON route contract, PTBI everywhere, page sections (Home, Campus Map, About Project, Our Team, Route History), no Data Notes, 5 portraits served, no external CDN |
| `test_deployment.py` | `render.yaml` (disk at `/var/data`, one instance, `app:app`, no secrets), Procfile, `.env`/database kept out of Git, Render disk detection, `/healthz`, **history survives a "redeploy"**, admin token, backup, security headers, About page shows a URL only when Render provides one |

`gunicorn ... app:app` was smoke-tested locally with `DATABASE_PATH` pointing at a separate folder: `/healthz` OK,
Eng'g Bldg. 2 → PTBI = 120.15 m, admin actions refused without the token. **Deployment to Render itself has not been
tested**, because it needs your accounts.

## 13. Limitations and what to measure next
- Distances are building-to-building (Excel). The line is traced from the maps, not surveyed.
- To route on walkways in metres, measure each walkway segment between junctions (purple and orange), each door connector and the 3 lawn links. Fill in `walkways.json → segments[].distance_m`, then use `build_walkway_graph(..., weight_field="distance_m")`.
- Three buildings (School Canteen, Engineering Research Bldg., Acad Bldg. 1) have no marked entrance.
- The Quadrangle path (`alt_quadrangle_01`) needs a site check: if the courtyard corridor of Eng'g Bldg. 1 is open to
  pedestrians, measure it, set `distance_m`, `is_walkable: true` and `verification_status` in `walkways.json`, and add
  the building's footprint exception for those segments.
- Check the 3 m and 8.52 m measurements on site.

## 14. References
- Cormen, T. H., Leiserson, C. E., Rivest, R. L., & Stein, C. (2022). *Introduction to Algorithms* (4th ed.). MIT Press.
- Dijkstra, E. W. (1959). A note on two problems in connexion with graphs. *Numerische Mathematik, 1*(1), 269–271.
- Sedgewick, R., & Wayne, K. (2011). *Algorithms* (4th ed.). Addison-Wesley.
- Python Software Foundation. (2024). *heapq — Heap queue algorithm*; *sqlite3 — DB-API 2.0 interface for SQLite databases*. Python 3.12 documentation.
- Pallets. (2024). *Flask documentation* (3.x).
- Render. (2026). *Persistent disks*; *Deploy for free*; *Blueprint YAML reference*; *Pricing*. https://render.com/docs
