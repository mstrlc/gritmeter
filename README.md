# gritmeter

**Surface check for every ride.** gritmeter reads the rides you upload to Strava, works out what you rode on, and
adds a short surface breakdown to the activity description:

```
cold start, bakery stop at 40 km.

surface by gritmeter.app
73% asphalt · 11% hardpack · 7% rough · 6% gravel
roughness 16 / 100, mixed
map data © OpenStreetMap contributors
```

Your own text stays as you wrote it. gritmeter only adds its block below, and replaces just that block if a ride is
analysed again.

**Status:** not live yet. [gritmeter.app](https://gritmeter.app) shows the landing page; connecting Strava is coming soon.

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
   |---|---|---|
   | 0 | asphalt | `surface=asphalt`, chipseal, untagged roads |
   | 1 | rough pavement | paving stones, setts, cobbles, concrete plates |
   | 2 | hardpack | compacted, fine gravel, `tracktype=grade2` |
   | 3 | gravel | loose gravel, unpaved, `tracktype=grade3` |
   | 4 | dirt | dirt, ground, grass, mud, sand |
   | 5 | rough | rock, bad smoothness, `grade4`–`grade5`, `mtb:scale` ≥ 2 |

   Ways with no usable tags count as **unknown**, and that share is always shown rather than guessed away.
6. **Score it.** The roughness score is the distance-weighted average grade, scaled to 0–100:

   | Score | Rating |
   |---|---|
   | 0–5 | road |
   | 5–10 | mostly road |
   | 10–20 | mixed |
   | 20–30 | gravel |
   | 30+ | rough gravel |

The classifier is tuned on rides in Czechia, so other regions may read less accurately at first.

## This repository

For now it holds only the website:

- `site/index.html`: the landing page for gritmeter.app. Plain HTML and CSS, no build step.
- `site/favicon.svg`: the gritmeter mark.

The analysis pipeline and the Strava integration will follow.

To view the site locally, open `site/index.html` in a browser, or serve the folder:

```bash
python3 -m http.server -d site
```

## Credits

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
gritmeter is not affiliated with or endorsed by Strava.
