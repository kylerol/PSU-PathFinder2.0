# PSU PathFinder – Frontend package

This package is the HTML/CSS/JavaScript half of PSU PathFinder. Every file in it is identical to the
same file in `PSU_PathFinder_Complete_Project.zip`.

| File | Role |
|---|---|
| `templates/index.html` | the page (Flask/Jinja template): header, Home, Campus Map, Route History, About Project, Our Team |
| `static/css/styles.css` | all styles; colours are CSS variables in `:root` (desktop-first, responsive, phone map mode) |
| `static/js/app.js` | map, SVG layers, search, route planner, bottom sheet, history – vanilla JavaScript, no CDN |
| `static/images/campus_map.png` | original campus map (legend cropped), the navigation background |
| `static/images/latest_annotated_campus_map.png`, `annotated_campus_map.png` | annotated maps aligned to the original |
| `static/images/proponent_photos/` | the five portraits from the approved proposal |
| `static/manifest.json`, `static/images/icon-*.png` | "Add to Home Screen" support |

## How the frontend talks to the backend
The page is served by Flask (`GET /`). `app.js` then loads every piece of data from the Flask API on the
same origin – it contains **no** building names, coordinates or distances of its own, and it never
calculates a route:

| Call | Used for |
|---|---|
| `GET /api/locations` | the 14 official locations and the map size |
| `GET /api/entrances` (`?building=<id>`) | blue entrance markers, building details |
| `GET /api/walkways` | purple original walkways (and walkway segments of the orange paths) |
| `GET /api/paths` | orange alternative paths, including the unverified Quadrangle path |
| `GET /api/footprints` | building footprints layer |
| `POST /api/route` `{"start","destination","exclude_flagged"}` | the shortest route calculated by Dijkstra in Python; the page draws the returned `route_geometry` |
| `GET /api/history?limit=24` | Route History section |
| `DELETE /api/history` (header `X-Admin-Token`) | clear history (admin only) |
| `GET /api/data-report` | the "measured pairs" figure on the home page |

The frontend cannot run on its own (for example by opening `index.html` directly), because the page
is a Flask template and all data come from the API. Unzip it into the same folder as the backend package
(or use the complete project) and run `python app.py`.
