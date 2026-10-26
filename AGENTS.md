# gritmeter

Rates how rough/gravelly a bike ride is by matching its GPS track to OpenStreetMap ways and classifying each way's surface.
Currently a set of local Python scripts; the goal is a Strava app that writes the result into each activity's description
for every connected athlete (see `PLAN.md` — read it before making design decisions).

## Pipeline
GPS points -> resample every 20 m -> fetch nearby OSM ways (cached tiles) -> snap each sample to the nearest way within 20 m
(with a penalty for sidewalks/footways) -> majority-filter over ~100 m -> classify tags -> sum distance -> roughness score.

| File | Role |
|---|---|
| `gritmeter/surface_mix.py` | Core: loading rides, OSM tile fetch/cache (Overpass or local), snapping/matching, 4-category `analyse`. Constants (`SAMPLE_M`, `SNAP_M`, tile size, tag sets) live here. |
| `gritmeter/ride_rating.py` | 6-level classifier (`classify_fine`), 0–100 roughness `score`, text `label`. Levels: asphalt, paved_rough, hardpack, gravel, dirt, rough (+ unknown). `rate(points)` is the one-call entry point; `description_block` / `merge_description` build and splice the Strava description block. |
| `gritmeter/strava.py` | Strava client: OAuth, token refresh, streams fetch, `process(activity)` (rate + description writer), rate-limit tracking, webhook subscription commands. |
| `gritmeter/store.py` | SQLite (WAL) store, `data/gritmeter.db`: `athletes` (tokens) and `jobs` (queue, dedupe on activity id, results). Keep storage access behind its functions. |
| `gritmeter/server.py` | Backend for my.gritmeter.app: routes, signed-cookie sessions + CSRF, webhook (`/webhook`), worker thread draining the queue, `maps` thread for new areas, `heartbeat` thread; catch-up pass only after an outage. |
| `gritmeter/web.py` | Server-rendered HTML (stdlib) for my.gritmeter.app: sign-in, rides list, settings. Styles copy `design/identity.html` tokens. |
| `site/index.html` | Landing page (plain HTML/CSS, no build step) + `site/favicon.svg`. |
| `wrangler.jsonc`, `src/worker.js` | Cloudflare Workers config for gritmeter.app: serves `site/`, redirects www to the apex. |
| `design/identity.html` | Visual identity sheet ("gritmeter"): tokens, type, logo, surface colours, components. Published as a claude.ai artifact. |
| `gritmeter/extract_osm.py` | Fills `data/osm/` tiles from Geofabrik `.osm.pbf` extracts (needs pyosmium, in `.venv`). |
| `.github/workflows/` | `ci.yml` checks PRs; `release.yml` deploys and tags every push to `main` (see Branches and releases). |
| `VERSION` | The next/current release, x.y.z; bumped in each release PR. |
| `CONTRIBUTING.md` | Branches, releases, hotfixes, deploy secrets and how CI reaches the Pi. |
| `host/` | Hosting for my.gritmeter.app on the Pi: `Dockerfile`, `compose.yaml`, `cloudflared.yml`, `firewall.sh` + `gritmeter-firewall.service`, `deploy.sh`. |

Data in `data/`: `rides.json` (id/date/name), `strava_polylines.json`, `osm/` tile cache (`<ty>_<tx>.json`),
`results.json` / `ratings.json` / `ratings.txt` (outputs), `fetch.log`. Rides with ids starting `validate_` are test fixtures
and are filtered out of reports.

## Commands
```bash
.venv/bin/python gritmeter/extract_osm.py data/pbf/*.osm.pbf   # build OSM tile cache from local extracts
python3 gritmeter/surface_mix.py  # analyse all rides (--fetch: only download/cache OSM data)
python3 gritmeter/ride_rating.py  # 6-level table + score per ride; writes data/ratings.json
python3 gritmeter/strava.py auth  # connect an athlete (needs STRAVA_CLIENT_ID/SECRET in env or .env)
python3 gritmeter/strava.py rate <activity_id> [--write]   # rate from streams; --write updates the description
python3 gritmeter/server.py [--dry-run]   # my.gritmeter.app on 127.0.0.1:8722; --dry-run rates but never writes descriptions
python3 gritmeter/strava.py subscribe <url>/webhook   # register the webhook once the server is reachable publicly
host/deploy.sh [--osm]            # rsync code (+ data/osm/) to pi4.local:/opt/gritmeter, docker compose up --build
npx wrangler deploy               # publish site/ (landing page) to Cloudflare Workers at gritmeter.app
```
No test suite yet. Validate classifier/snapping changes by re-running `gritmeter/ride_rating.py` and comparing against
`data/ratings.txt`.

## Branches and releases
`main` is what is running: every push to it deploys both targets and tags the commit as a GitHub release.
`CONTRIBUTING.md` has the full process (release steps, hotfixes, failed runs, how CI reaches the Pi); in short:
- Work goes on `feat/<thing>` (or `fix/`, `chore/`) branched from `develop`, with PRs into `develop`.
- A release is a PR from `develop` into `main` that bumps `VERSION` (x.y.z); merge it with a merge commit, not a
  squash, so `develop` stays an ancestor of `main`. `ci.yml` checks the source branch and that the version is new.
- On merge, `release.yml` deploys web and Pi, checks both, then creates release `vVERSION` with generated notes.
- An urgent fix: `hotfix/<thing>` from `main`, PR into `main` with a patch bump, then merge `main` back into `develop`.
- Secrets live in the GitHub `production` environment: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`,
  `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE` (Tailscale workload identity, tag `tag:ci`), `PI_SSH_KEY`; variable `PI_HOST`
  (the Pi's tailnet IP). `.github/pi_known_hosts` pins the Pi's host key.

## Deploying
Two targets, deployed by `release.yml` on every push to `main`. Manual deploys (below, and the `/deploy` skill in
`.claude/skills/deploy/`) are for OSM tiles and emergencies only, and only from `main`.

| Target | Serves | Deploy | Changed files that need it |
|---|---|---|---|
| web | gritmeter.app (Cloudflare Workers) | `npx wrangler deploy` | `site/**`, `src/worker.js`, `wrangler.jsonc` |
| Pi | my.gritmeter.app (Docker on `pi4.local`, Cloudflare Tunnel) | `host/deploy.sh` (`--osm` for new tiles) | `gritmeter/**`, `host/**`, `site/strava/` |

- Deploy by hand only when asked, from a clean `main` that matches `origin/main`: `host/deploy.sh` rsyncs the working
  tree as it is, and wrangler uploads `site/` as it is. New OSM tiles always need `host/deploy.sh --osm` from the Mac.
- A Pi deploy restarts the live app: if the Pi is not in `--dry-run`, it rates and writes real descriptions as soon as it
  is back up. Never change `SERVER_FLAGS` in the Pi's `.env` as part of a deploy.
- After `host/firewall.sh` changes, also run `ssh pi4.local sudo /opt/gritmeter/firewall.sh` (needs the user's sudo).
- Verify after deploying: web, `curl -s https://gritmeter.app/` shows the change; Pi, `docker compose ps` lists both
  containers up and `curl -s https://my.gritmeter.app/health` says `ok`.

## Conventions and gotchas
- Input to the analysis is just a list of `(lat, lon)` points. Strava data must come from the **streams API**
  (`latlng`), not `map.polyline` (simplified, too coarse) and not GPX (no API export).
- Surface classification is tuned for Czech OSM tagging (e.g. bare `surface=paved` is treated as asphalt; untagged roads
  are inferred from highway class / tracktype). Keep that inference visible via the "unknown" share rather than hiding it.
- Tiles are 0.02° x 0.03° (~2.2 km). Always write empty tiles to the cache so a miss is never re-fetched.
  Never cache an Overpass answer that carries a `remark` (timed out / out of memory: partial or empty). In the server,
  rides needing uncached tiles go to status `mapping` and the `maps` thread fetches them, never the worker itself.
- Never overwrite a user's Strava description; append after a marker and replace only our own block, in place.
  Other apps write the same field (last write wins): re-read right before any PUT, never reuse an older read.
- OSM-derived output needs the "© OpenStreetMap contributors" credit; Strava UI needs "Powered by Strava".
- Product name is **gritmeter** (domain gritmeter.app). Follow `design/identity.html` for any UI:
  Epilogue (900 headlines, 400 body) + DM Mono only for what gritmeter writes and data values, never for UI labels;
  paper/ink neutrals; one accent, rough red `#c42e2a` (dark `#ee5a4e`); light and dark follow the system
  theme, no switcher (decided 2026-10-25). Corners: rounded
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
- Starting `gritmeter/server.py` without `--dry-run` writes to real descriptions of rides uploaded after the athlete connected
  (after an outage, catch-up lists each rider's rides from 3 days before it began). Rides rated in dry-run (status `dryrun`) are never written
  unless the rider clicks add once the server is live; in dry-run the rides page shows no per-ride buttons.
- Strava ids differ from Garmin ids: ride `24641046424` in `data/rides.json` is Strava `20490259741`.
- Runs on a Raspberry Pi 4 (`pi4.local`) in Docker (everything in `host/`): keep runtime memory
  small and the image stdlib-only; do heavy `.pbf` processing on the Mac. `data/` and `.env` live on the Pi, never in
  the image. `host/deploy.sh` copies `host/`'s files to the top of `/opt/gritmeter`, with `gritmeter/` and `site/` beside
  them, so paths on the Pi have no `host/` in them. The app starts in `--dry-run` unless the Pi's `.env` sets `SERVER_FLAGS=` (empty).
  `firewall.sh` (run at boot by `gritmeter-firewall.service`, installed once on the Pi) keeps the containers on the
  `br-gritmeter` bridge off the Pi, the LAN and the tailnet: internet only, so they use public DNS (`dns:` in
  `compose.yaml`). Re-run `sudo /opt/gritmeter/firewall.sh` after editing it.
- Plain scripts, standard library where possible, small functions with short docstrings; match the existing style.
