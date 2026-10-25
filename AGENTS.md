# Pathfinder

Rates how rough/gravelly a bike ride is by matching its GPS track to OpenStreetMap ways and classifying each way's surface.
Currently a set of local Python scripts; the goal is a Strava app that writes the result into each activity's description
for every connected athlete (see `PLAN.md` — read it before making design decisions).

## Pipeline
GPS points -> resample every 20 m -> fetch nearby OSM ways (cached tiles) -> snap each sample to the nearest way within 20 m
(with a penalty for sidewalks/footways) -> majority-filter over ~100 m -> classify tags -> sum distance -> roughness score.

| File | Role |
|---|---|
| `surface_mix.py` | Core: loading rides, OSM tile fetch/cache (Overpass or local), snapping/matching, 4-category `analyse`. Constants (`SAMPLE_M`, `SNAP_M`, tile size, tag sets) live here. |
| `ride_rating.py` | 6-level classifier (`classify_fine`), 0–100 roughness `score`, text `label`. Levels: asphalt, paved_rough, hardpack, gravel, dirt, rough (+ unknown). `rate(points)` is the one-call entry point; `description_block` / `merge_description` build and splice the Strava description block. |
| `strava.py` | Strava client: OAuth, token refresh, streams fetch, `process(activity)` (rate + description writer), rate-limit tracking, webhook subscription commands. |
| `store.py` | SQLite (WAL) store, `data/gritmeter.db`: `athletes` (tokens) and `jobs` (queue, dedupe on activity id, results). Keep storage access behind its functions. |
| `server.py` | Backend for my.gritmeter.app: routes, signed-cookie sessions + CSRF, webhook (`/webhook`), worker thread draining the queue, catch-up pass every 6 h. |
| `web.py` | Server-rendered HTML (stdlib) for my.gritmeter.app: sign-in, rides list, settings. Styles copy `design/identity.html` tokens. |
| `make_map.py` | `build(ride)` -> render-ready dict; `main` writes a Leaflet map to `maps/<id>.html` from `map_template.html`. |
| `map_template.html` | Ride map page (Leaflet); surface colours, dash patterns and G0–G5 labels come from `COLORS` / `DASH` / `GRADES` in `make_map.py`. |
| `site/index.html` | Landing page (plain HTML/CSS, no build step) + `site/favicon.svg`. |
| `design/identity.html` | Visual identity sheet ("gritmeter"): tokens, type, logo, surface colours, components. Published as a claude.ai artifact. |
| `extract_osm.py` | Fills `data/osm/` tiles from Geofabrik `.osm.pbf` extracts (needs pyosmium, in `.venv`). |

Data in `data/`: `rides.json` (id/date/name), `strava_polylines.json`, `osm/` tile cache (`<ty>_<tx>.json`),
`results.json` / `ratings.json` / `ratings.txt` (outputs), `fetch.log`. Rides with ids starting `validate_` are test fixtures
and are filtered out of reports.

## Commands
```bash
.venv/bin/python extract_osm.py data/pbf/*.osm.pbf   # build OSM tile cache from local extracts
python3 surface_mix.py            # analyse all rides (--fetch: only download/cache OSM data)
python3 ride_rating.py            # 6-level table + score per ride; writes data/ratings.json
python3 make_map.py [ride_id]     # map for one ride (default: newest) -> maps/<id>.html
python3 strava.py auth            # connect an athlete (needs STRAVA_CLIENT_ID/SECRET in env or .env)
python3 strava.py rate <activity_id> [--write]   # rate from streams; --write updates the description
python3 server.py [--dry-run]     # my.gritmeter.app on 127.0.0.1:8722; --dry-run rates but never writes descriptions
python3 strava.py subscribe <url>/webhook        # register the webhook once the server is reachable publicly
```
No test suite yet. Validate classifier/snapping changes by re-running `ride_rating.py` and comparing against
`data/ratings.txt`, and by eye on a map (`make_map.py`).

## Conventions and gotchas
- Input to the analysis is just a list of `(lat, lon)` points. Strava data must come from the **streams API**
  (`latlng`), not `map.polyline` (simplified, too coarse) and not GPX (no API export).
- Surface classification is tuned for Czech OSM tagging (e.g. bare `surface=paved` is treated as asphalt; untagged roads
  are inferred from highway class / tracktype). Keep that inference visible via the "unknown" share rather than hiding it.
- Tiles are 0.02° x 0.03° (~2.2 km). Always write empty tiles to the cache so a miss is never re-fetched.
- Never overwrite a user's Strava description; append after a marker and replace only our own block, in place.
  Other apps write the same field (last write wins): re-read right before any PUT, never reuse an older read.
- OSM-derived output needs the "© OpenStreetMap contributors" credit; Strava UI needs "Powered by Strava".
- Product name is **gritmeter** (domain gritmeter.app). Follow `design/identity.html` for any UI:
  Epilogue (900 headlines, 400 body) + DM Mono only for what gritmeter writes and data values, never for UI labels;
  paper/ink neutrals; one accent, rough red `#c42e2a` (dark `#ee5a4e`); light + dark + auto themes. Corners: rounded
  buttons, cards and segmented controls (8/12/18 px; `--r-sm`/`--r`/`--r-lg` in `web.py`), on the landing page and in
  my.gritmeter.app alike (decided 2026-10-25, replacing the identity sheet's square corners).
- Copy is lowercase except proper nouns (Strava, OpenStreetMap, GPS, place names); in running text write
  gritmeter as the small mark + bold wordmark.
- Description block format is fixed in `PLAN.md`: `surface: ...`, `roughness N / 100, label`, then the sign-off
  `- from gritmeter.app · map data © OpenStreetMap contributors`; `ride_rating.BLOCK_PATTERNS` finds it (and the older
  4-line block).
- Strava tokens/secrets: environment variables or an untracked file only; never commit or log them (server.py silences
  request logging because query strings carry OAuth codes). `.env` keys: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET,
  STRAVA_VERIFY_TOKEN, SESSION_SECRET; server also reads HOST, PORT, PUBLIC_URL.
- Starting server.py without `--dry-run` writes to real descriptions of rides uploaded after the athlete connected
  (catch-up covers the last 3 days). Rides rated in a test run are never written unless the rider clicks add.
- Strava ids differ from Garmin ids: ride `24641046424` in `data/rides.json` is Strava `20490259741`.
- Target hardware may be a Raspberry Pi (3B/4, 64-bit OS): keep runtime memory small; do heavy `.pbf` processing on the Mac.
- Plain scripts, standard library where possible, small functions with short docstrings; match the existing style.
