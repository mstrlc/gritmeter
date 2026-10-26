<p align="center">
  <img src="site/favicon.svg" width="76" alt="gritmeter">
</p>

<h1 align="center">gritmeter</h1>

<p align="center">
  <b>Surface check for every ride.</b> gritmeter reads the rides you upload to
  Strava, works out what you rode on, and adds a short surface breakdown to the
  activity description.
  <br><br>
  <a href="https://gritmeter.app">gritmeter.app</a> ·
  <a href="https://my.gritmeter.app">my.gritmeter.app</a> ·
  <a href="LICENSE">AGPL-3.0</a>
</p>

---

```
cold start, bakery stop at 40 km.
surface: 73% asphalt · 11% hardpack · 7% rough · 6% gravel
roughness 16 / 100, mixed
- from gritmeter.app · map data © OpenStreetMap contributors
```

Your own text stays as you wrote it. gritmeter only adds its block below, and
replaces just that block if a ride is analysed again.

**Status:** live, in early testing. [gritmeter.app](https://gritmeter.app) is the landing
page; riders connect Strava and manage their rides at
[my.gritmeter.app](https://my.gritmeter.app), which rates new rides and writes the result
into their descriptions. Strava caps the app at 10 athletes for now.

## How it works

1. **Get the track.** The full-resolution GPS stream of the ride comes from the Strava API.
2. **Sample it.** The track is resampled to a point every 20 metres.
3. **Match it to the map.** Each point snaps to the nearest road or path on OpenStreetMap within 20 metres, with a
   penalty for sidewalks and footways so a ride on the road doesn't snap to the pavement beside it.
4. **Smooth it.** A majority filter over about 100 metres stops one stray point from flipping a result where a track
   crosses a road.
5. **Classify the surface.** Each way's OSM tags (`surface`, `tracktype`, `smoothness`, `highway`, `mtb:scale`) put it
   into one of six grades:

   | Grade | Surface | Typical tags |
   | --- | --- | --- |
   | 0 | asphalt | `surface=asphalt`, chipseal, untagged roads |
   | 1 | rough pavement | paving stones, setts, cobbles, concrete plates |
   | 2 | hardpack | compacted, fine gravel, `tracktype=grade2` |
   | 3 | gravel | loose gravel, unpaved, `tracktype=grade3` |
   | 4 | dirt | dirt, ground, grass, mud, sand |
   | 5 | rough | rock, bad smoothness, `grade4`–`grade5`, `mtb:scale` ≥ 2 |

   Ways with no usable tags count as **unknown**, and that share is always shown rather than guessed away.
6. **Score it.** The roughness score is the distance-weighted average grade, scaled to 0–100:

   | Score | Rating |
   | --- | --- |
   | 0–5 | road |
   | 5–10 | mostly road |
   | 10–20 | mixed |
   | 20–30 | gravel |
   | 30+ | rough gravel |

The classifier is tuned on rides in Czechia, so other regions may read less accurately at first.

## Running it

**Plain Python, standard library only** — no framework, no build step. That's
load-bearing: the app runs in Docker on a Raspberry Pi 4 and has to stay small.
Heavy `.pbf` processing happens on the Mac.

```sh
.venv/bin/python gritmeter/extract_osm.py data/pbf/*.osm.pbf   # build the OSM tile cache
python3 gritmeter/ride_rating.py                               # grade every ride in data/rides.json
python3 gritmeter/strava.py rate <activity_id>                 # rate one Strava ride (--write updates it)
python3 gritmeter/server.py --dry-run                          # my.gritmeter.app on 127.0.0.1:8722
python3 -m http.server -d site                                 # the landing page
```

Strava keys go in `.env` (`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`,
`STRAVA_VERIFY_TOKEN`, `SESSION_SECRET`), never in the repo.

| Path | What |
| --- | --- |
| `gritmeter/surface_mix.py` | OSM tile fetch and cache, snapping the track to OSM ways |
| `gritmeter/ride_rating.py` | the six grades, the roughness score and the description block |
| `gritmeter/strava.py` | Strava OAuth, GPS streams, the description writer, webhook subscription |
| `gritmeter/store.py` | SQLite store: athletes and the job queue |
| `gritmeter/server.py` | my.gritmeter.app: routes, sessions, webhook, worker, map and heartbeat threads |
| `gritmeter/web.py` | server-rendered HTML for my.gritmeter.app |
| `gritmeter/extract_osm.py` | fills `data/osm/` from Geofabrik extracts (needs pyosmium) |
| `site/` | the landing page for gritmeter.app, plain HTML and CSS; `site/strava/` holds Strava's official assets, unmodified |
| `wrangler.jsonc`, `src/worker.js` | Cloudflare Workers: serves `site/`, redirects www to the apex |
| `host/Dockerfile`, `host/compose.yaml` | the app plus `cloudflared`, no published ports; `data/` and `.env` mounted from the Pi |
| `host/cloudflared.yml` | the tunnel's ingress, my.gritmeter.app to the app container |
| `host/firewall.sh` | keeps the containers off the Pi, the LAN and the tailnet: internet only |
| `host/deploy.sh` | copies the code to the Pi and rebuilds there (`--osm` also copies the tile cache) |
| `design/identity.html` | the visual identity: tokens, type, logo, surface colours |

Two deploys, both by hand; pushing to `main` deploys nothing. The landing page
goes to Cloudflare Workers with `npx wrangler deploy`, the app to the Pi with
`host/deploy.sh`. The app starts in dry-run (rates rides, writes no descriptions)
until the Pi's `.env` sets `SERVER_FLAGS=` (empty).

## Before you change anything

- **[`AGENTS.md`](AGENTS.md)** — the pipeline, every command, and the things that
  are easy to get wrong: streams not polylines, never overwrite a rider's text,
  never cache a partial Overpass answer.
- **[`PLAN.md`](PLAN.md)** — the roadmap and the decisions behind it, including the
  fixed description block format.
- **[`design/identity.html`](design/identity.html)** — type, colour and the mark.
  Any UI follows it.

## License

Copyright (c) 2026 Matyáš Strelec. The code is under the [GNU Affero General Public License v3.0](LICENSE): you can
use, change and share it, and if you run a modified version as a public service, you must publish your changes under
the same licence. OpenStreetMap data used by gritmeter remains under the [ODbL](https://www.openstreetmap.org/copyright).

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
gritmeter is not affiliated with or endorsed by Strava.
