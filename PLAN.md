# Pathfinder → Strava app: plan

Decisions from the 2026-10-11 discussion. Size/time figures are rough estimates, not measurements.

## Goal
When a rider uploads a ride to Strava, classify its surfaces (OSM-based, existing pipeline) and
**append a short summary to the activity description — for every connected athlete**.
A web backend + frontend for browsing rides comes later, on top of the same backend.

## Input data: Strava streams, not GPX
- Strava's API has no GPX export, and `map.polyline` is simplified/lossy (too coarse for 20 m sampling).
- Use `GET /activities/{id}/streams?keys=latlng,time,distance&key_by_type=true` → full-resolution points.
- Maps straight onto `ride["points"]`; `surface_mix.py` / `ride_rating.py` should not need to change.
- Scopes: `activity:read` (+ `activity:read_all` for private rides), `activity:write` for the description.
- First check: reproduce the stored score for ride `24641046424` (Garmin id; Strava `20490259741`) from streams.

## Backend flow
```
Strava webhook (activity created) -> POST /webhook (200 in <2 s, enqueue only)
  -> worker: refresh token -> GET streams -> analyse(points) -> store result -> PUT description
```
- Append, never overwrite: add our block after the rider's text; it is recognised by its `surface: ...` and
  `roughness N / 100` lines and its `- from gritmeter.app` sign-off (the older 4-line block too), and re-runs replace
  only that block, where it stands.
- Other description apps (KlimatApp, Wandrer, ...) write the same field and Strava has no conditional update, so the
  last write wins (decided 2026-10-25): new uploads wait 3 min so they write first; the description is re-read right
  before our PUT; one follow-up check 15 min later restores our block if another app replaced the whole description
  (only while the ride's status is still "done", so a rider's removal via my.gritmeter.app sticks; a manual delete on
  Strava within those 15 min is undone once). Our block takes the description's line endings (others write `\r\n`)
  and, like theirs, follows the previous text on the next line.
- Dedupe on `(athlete_id, activity_id)`; Strava retries webhooks.
- Skip activities without `latlng` (indoor / virtual).
- Per-athlete tokens (6 h access). *Currently plain text* in `data/gritmeter.db` (mode 600); encrypting the refresh token
  needs a non-stdlib library (`cryptography`) and a key on the same Pi, so it is deferred.
- Catch-up job: list recent activities, process any not yet seen (covers downtime; Strava drops events after a few retries).
- Rate limits are per *app*: this app has 200 req/15 min, 2000/day (seen 2026-10-24); ~5 calls per written ride (activity,
  streams, re-read, PUT, follow-up check) => ~400 rides/day total until raised. The worker pauses at 90% of either window.
- Writing for others needs Strava approval beyond single-athlete mode — apply early (longest wait). Re-check current API terms
  (2024 agreement restricts showing one user's data to others and use for AI/ML).
- Attribution: "© OpenStreetMap contributors" in the description; "Powered by Strava" in any UI.
- Description format (decided 2026-10-25, replacing the 4-line block with a header from 2026-10-24), plain text,
  lowercase except proper nouns, in the style of KlimatApp/Wandrer (no blank line, sign-off last):
  ```
  surface: 73% asphalt · 11% hardpack · 7% rough · 6% gravel
  roughness 16 / 100, mixed
  - from gritmeter.app · map data © OpenStreetMap contributors
  ```
  Line 1 lists surfaces largest first, leaving out shares under 5% (`ride_rating.MIN_PCT`); paved_rough reads
  "rough pavement". Score rounded to
  a whole number, then the `ride_rating.label`.

## Hosting: Raspberry Pi
- Pi 4: fine. Pi 3B (1 GB): fine for SQLite + worker; not for PostGIS/Valhalla or building tiles from a .pbf.
- 64-bit Raspberry Pi OS (pyosmium wheels). Build OSM tiles on the Mac, copy `data/osm/` over.
- Public webhook URL without port forwarding: Cloudflare Tunnel (or Tailscale Funnel).
- Domain: **gritmeter.app** (bought 2026-10-24). Landing page at the root; webhook and OAuth callback on the same domain
  (Strava's "Authorization Callback Domain" = `gritmeter.app`). `.app` is HTTPS-only (HSTS preloaded), so every
  hostname needs a valid certificate; the tunnel provides one.
- SQLite (WAL) on SSD/good card; keep storage behind a small interface so Postgres can replace it later.
- Docker (decided 2026-10-25): the Pi (`pi4.local`, Pi 4 4 GB, Raspberry Pi OS bullseye 64-bit) already runs its other
  services in Docker, so gritmeter is its own compose project in `/opt/gritmeter` (`compose.yaml`: the app on
  `python:3.12-slim` + `cloudflared`), with no published ports and not on the Pi's shared proxy
  network. `deploy.sh` rsyncs the code and rebuilds on the Pi (no registry); `data/` and `.env` stay on the Pi.
  The container runs as uid 1000 (the Pi's login user, who owns `data/`). Starts in `--dry-run` until `.env` sets
  `SERVER_FLAGS=` (empty). Cheaper than a VPS (~€50/yr) since the Pi runs anyway; outages only delay descriptions
  (webhook retries + 3-day catch-up), and moving later is copying one folder.
- Tunnel (2026-10-25): locally managed tunnel `gritmeter`, made with the `cloudflared` CLI on the Mac (`tunnel create`,
  `tunnel route dns gritmeter my.gritmeter.app`); ingress lives in `cloudflared.yml` in the repo, and the Pi's `.env`
  holds only the run token (`cloudflared tunnel token gritmeter`). The account cert (`~/.cloudflared/cert.pem`) stays
  on the Mac.

## OSM data strategy: regional + lazy, not Europe/NA at once
Source sizes (Geofabrik .pbf, approx): Europe ~30 GB, NA ~15 GB, planet ~75 GB; highway-only is far smaller.
Full continents are a bad first step: hours of processing with a big node index, millions of ~2.2 km JSON tiles,
and most tiles would never be read.

| Phase | What |
|---|---|
| 1 | Home region (CZ + SK, AT, DE, PL) via `extract_osm.py` on the Mac |
| 2 | On-demand fallback: cache miss -> fetch tile from Overpass, cache it (empty results too), with backoff |
| 3 | Replace JSON tile files with one SQLite/GeoPackage + spatial index |
| 4 | Self-hosted Overpass/PostGIS for Europe/NA — only if real users make public Overpass a bottleneck |

Uncovered tiles show as "unknown" until fetched; first ride in a new place is slower.

## Build order
1. Package the pipeline as `analyse(points) -> {pct, score, label}`; remove CZ-specific assumptions; make the tile source pluggable.
2. Strava client: OAuth, token refresh, streams fetcher; verify against ride `24641046424`.
   *Status 2026-10-24:* `ride_rating.rate` + description helpers and `strava.py` written; offline check on the GPX of
   `24641046424` reproduces the stored 16.5. Live check done: `24641046424` is the *Garmin* id; the same ride on Strava
   is `20490259741`, and its streams (7048 points, 37.2 km) give 16.6 with the same mix. Description writing not yet tried.
3. Webhook + queue + description writer (marker/append, dedupe, catch-up job).
   *Status 2026-10-24:* `server.py` + `store.py` written and tested locally in `--dry-run` (validation, dedupe, unknown
   athlete, delete event, catch-up skipping non-rides, rate-limit pause). Catch-up only looks at rides uploaded after the
   athlete connected; older rides are never written unless the rider asks.
4. **my.gritmeter.app** (decided 2026-10-24): the rider's side of the product, served by the same `server.py` on the Pi.
   - Sign in = Connect with Strava; a signed session cookie (`SESSION_SECRET`, 30 days, HttpOnly, SameSite=Lax, Secure
     on https) plus a CSRF token on every form. No passwords, no server-side sessions.
   - Rides page: every activity gritmeter saw, newest first, with score, rating, grade bar + text, status, View on
     Strava, and per-ride actions (add to description / remove from description / try again). "rate my last 14 days"
     rates past rides without writing (status `rated`, "past ride").
   - Settings: auto-add on/off; leave "only you" rides alone; leave commutes (Strava's commute flag) alone. Disconnect revokes on Strava and deletes all our data.
   - All Strava calls for a ride stay in the worker (rate limits); the pages only queue requests.
   *Status 2026-10-24:* `web.py` + routes written and tested locally in `--dry-run` (sign-in page, rides, settings save,
   sign-out, CSRF/forged-cookie/ownership checks, backfill of 18 activities, phone width, both themes). Disconnect not
   exercised against Strava (it would revoke the real authorization).
   *Status 2026-10-25:* deployed on the Pi (Docker, see Hosting) in `--dry-run`, reachable at https://my.gritmeter.app
   through the tunnel; Strava's authorize page accepts the `my.gritmeter.app/callback` redirect (sign-in itself not yet
   tried there).
   Next: sign in on the Pi, subscribe the webhook (`https://my.gritmeter.app/webhook` via `strava.py subscribe`), stop
   local servers sharing the same tokens, then turn off dry-run (`SERVER_FLAGS=`); the official Connect button (links straight to
   Strava's authorize URL, as the brand guidelines require) and Powered by Strava logo are in `site/strava/`; the
   landing page shows "coming soon" until the app is reachable; then link its buttons to my.gritmeter.app.
5. Ride map page per activity (a Leaflet prototype lives on the `feat/ride-map` branch).

## Open questions
- Personal-only vs. open to other riders (drives Strava approval and Phase 4).
