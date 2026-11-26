# PSU PathFinder – Current Progress Report

**Presentation date:** [INSERT ACTUAL PRESENTATION DATE]  
**Status recorded:** 26 November 2026  
**Public website:** [INSERT ACTUAL RENDER WEBSITE URL] (not deployed yet)

Group 3 · Data Structures and Algorithms · Pangasinan State University – Urdaneta Campus  
Matthew David Q. Caoile · Frederick Michael D. Cabanilla · Keizer Alexus G. Paltep · Frankie Mae M. Damian · Jholan Froid Andrei P. Ramirez

## 1. Summary

- The website runs locally (`python app.py` → http://127.0.0.1:5000) with the actual campus map, 14 locations (PTBI), the Main Gate, 17 entrances, purple original walkways and orange alternative paths.
- Shortest routes use a hand-written Dijkstra (heapq min-heap, adjacency list, dictionaries) on the 91 measured Excel pairs; route lines follow walkways and never cross a building.
- SQLite stores everything, including route history. **126 automated tests pass.**
- The new orange **Quadrangle path** (`alt_quadrangle_01`) is stored and shown, but is **unverified and not used for routing** because it is drawn over Engineering Bldg. 1.
- GitHub + Render deployment is **prepared but not yet carried out** (needs the group's accounts and a paid Render instance with a persistent disk).

## 2. Feature status

| Feature | Status | Description / remaining work | Evidence |
|---|---|---|---|
| Official building registry | Completed | 14 locations with fixed IDs, names and legend colours; building 8 is **PTBI** and the legacy spelling “PBTI” maps to the same record. | locations.json; buildings table (14 rows); test_data.py, test_api.py |
| Map preparation | Completed | Original map cropped to 944 × 1472 px; three annotated maps cropped of black margins and aligned (≤ 0.81 px RMS error). | prepare_map.py; audit passed; Figures 1–2 |
| Main Gate and entrance markers | Completed | Red dot registered as the Main Gate; 17 blue-dot entrances registered and matched within 1.6 px. | entrances.json; entrances table (17 rows) |
| Original (purple) walkways | Completed | 7 purple segments plus 21 traced paved-road segments stored as original_walkway. | walkways.json; audit: 97.3 % of purple pixels modelled |
| Orange alternative paths | Completed | 2 alternative paths (6 segments): between Acad Bldg. 1 and 2, and behind Engineering Bldg. 1. | alternative_paths table; audit: 98.5 % of orange pixels modelled |
| Excel distance import | Completed | 91 pairs; 91 main and 36 alternative distances; N/A kept as unavailable, never zero. | data_loader.py; walking_routes (127 rows); test_data.py (25 tests) |
| SQLite database | Completed | Nine required tables plus schema_version; duplicate-safe imports; route history kept across restarts. | database.py; test_database.py (19 tests) |
| Weighted graph and Dijkstra (heapq) | Completed | Hand-written Dijkstra on adjacency lists; equals Floyd–Warshall for all 182 ordered pairs. | graph.py; test_routing.py (19 tests); Figure 10 |
| Route lines avoid buildings | Completed | Every segment checked against 20 footprints; all 182 route lines are collision-free. | test_map_geometry.py (30 tests); Figure 4 |
| Flask API and website | Completed | Home, Campus Map, About Project, Our Team, Route History; search, selectors, swap, legend; desktop and phone layouts. | app.py, templates, static; test_api.py (15 tests); Figures 3–8 |
| Quadrangle alternative path | Partially Implemented | alt_quadrangle_01 traced, stored and shown dashed; drawn over the Engineering Bldg. 1 footprint, so excluded from routing; distance NULL. Remaining: confirm the corridor is open to pedestrians and measure it. | walkways.json; QuadrangleAlternativePathTests; Figure 5 |
| Entrance verification | Partially Implemented | 3 entrance assignments are unverified; School Canteen, Engineering Research Bldg. and Acad Bldg. 1 have no marked entrance. | entrances.json (verification_status) |
| Walkway segment distances | Partially Implemented | Total distances come from measured building pairs; walkway segments have no on-site lengths, so route lines are traced geometry (geometry_verified = false). | walkway_segments.distance_m = NULL |
| Flagged measurements | Partially Implemented | CAE Bldg. ↔ New Engineering Bldg. 3 m and Teacher's Education ↔ DOST 8.52 m kept but flagged; users can exclude them. | walking_routes (2 flagged); test_data.py |
| Deployment configuration | Partially Implemented | render.yaml, Procfile, Gunicorn entry point, /var/data disk path, health check and admin token prepared; Gunicorn tested locally. Remaining: create the service. | render.yaml; test_deployment.py (18 tests) |
| GitHub repository | Planned | Create the PSU-PathFinder repository and push the project. | – |
| Render deployment and public URL | Planned | Create a paid Render web service with a 1 GB persistent disk at /var/data; record the URL: [INSERT ACTUAL RENDER WEBSITE URL]. | – |
| Online verification | Planned | Confirm persistence after restart and redeployment, and access with the proponents' laptops switched off. | – |
| On-site measurements | Planned | Measure the walkway segments and the Quadrangle path; re-check the 3 m and 8.52 m values. | – |
| User testing | Planned | Usability test with freshmen, transferees and visitors. | – |

## 3. Test results (26 November 2026)

| File | Tests | Result |
|---|---|---|
| test_api.py | 15 | passed |
| test_data.py | 25 | passed |
| test_database.py | 19 | passed |
| test_deployment.py | 18 | passed |
| test_map_geometry.py | 30 | passed |
| test_routing.py | 19 | passed |
| **Total** | **126** | **all passed** |

Map annotation audit (`python prepare_map.py --audit`): red dot 1.5 px from the Main Gate; all 17 blue dots within 1.6 px; 97.3 % of purple and 98.5 % of orange pixels on modelled walkways → AUDIT PASSED.

Not yet tested: the Render deployment, the public URL, persistence on Render, and use with the proponents' laptops switched off.

## 4. Suggested demo for the progress presentation

1. Home page → *Start Navigation*.
2. Engineering Bldg. 1 → PTBI: 70.71 m (measured alternative, "exit at back"); open *How Dijkstra found it*.
3. Engineering Bldg. 2 → PTBI: 120.15 m along the orange path behind Engineering Bldg. 1.
4. Legend: toggle purple / orange layers; click the dashed Quadrangle path and explain why it is not routed.
5. *Latest* button: compare overlays with the hand-drawn annotated map.
6. Route History (saved in SQLite) → About Project → Our Team.
7. Phone: same site on a phone (`python app.py --lan`).
8. Show `python -m pytest tests -q` → 126 passed.

## 5. Next steps

1. Create the GitHub repository `PSU-PathFinder` and push (README section 2.3).
2. Create the Render service from `render.yaml` (paid instance + 1 GB disk at `/var/data`; check the cost first).
3. Verify `/healthz` shows `persistent_storage: true`, redeploy, and confirm the history is kept; then write the URL into the paper.
4. Measure walkway segments and the Quadrangle corridor; re-check the 3 m and 8.52 m values; verify the 3 unverified entrances.
5. Short usability test with freshmen and visitors.
