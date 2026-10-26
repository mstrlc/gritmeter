---
name: deploy
description: Deploy gritmeter. "web" publishes the landing page (site/) to Cloudflare Workers at gritmeter.app; "pi" ships the app to the Raspberry Pi behind my.gritmeter.app. Use when the user says "/deploy", "deploy", "ship it", "deploy the site", "deploy to the pi" or similar.
---

# Deploy gritmeter

Normally nothing is deployed by hand: merging a release PR into `main` runs `.github/workflows/release.yml`, which
deploys both targets and tags the release. To "ship" features, open (or merge, if asked) the `develop` -> `main` PR
with a `VERSION` bump. Use this skill for new OSM tiles (`pi --osm`), when the workflow is broken, or when the user
explicitly asks for a manual deploy; never deploy anything that is not on `origin/main`.

Two targets, each deployed on its own. Argument: `web`, `pi`, `pi --osm`, or nothing (pick from what changed).

| Target | Serves | Command | Needed for changes to |
|---|---|---|---|
| web | gritmeter.app, Cloudflare Workers | `npx wrangler deploy` | `site/**`, `src/worker.js`, `wrangler.jsonc` |
| pi | my.gritmeter.app, Docker on `pi4.local` via Cloudflare Tunnel | `host/deploy.sh` | `gritmeter/**`, `host/**`, `site/strava/` |

`site/strava/` goes to both (the app serves the Strava logos too). New OSM tiles in `data/osm/` need `host/deploy.sh --osm`.

## 1. Pick the target
With no argument, compare against what is live: the latest GitHub release (`gh release view --json tagName`) is what
`release.yml` last deployed, so `git diff --stat <tag>..main`; ask if it is unclear. Deploy only the targets whose files
changed; if both did, do both (web first, it is the quicker one to check).

## 2. Preflight
- `git status --short` is empty and the branch is `main`. Both tools ship the working tree as it is, so uncommitted
  edits would go live without being in git. If it is dirty, stop and ask (commit first, or deploy anyway).
- `main` matches `origin/main` (`git fetch && git status -sb`). A local commit not on `origin` would go live outside
  the release flow; stop and ask.

## 3a. web
```bash
npx --yes wrangler deploy
```
Output lists the uploaded assets (only changed files upload) and both custom domains. Then check the live page:
```bash
curl -s https://gritmeter.app/ | grep -c '<something from the change>'
curl -s -o /dev/null -w '%{http_code}\n' https://www.gritmeter.app/   # 301 to the apex
```

## 3b. pi
The app restarts. If the Pi's `.env` has `SERVER_FLAGS=` (empty), it is live and starts writing real Strava
descriptions as soon as it is up, so say which mode it is in when reporting. Never edit `SERVER_FLAGS` as part of a deploy.
```bash
host/deploy.sh         # or host/deploy.sh --osm after rebuilding tiles on the Mac
```
It rsyncs `host/`'s files to the top of `/opt/gritmeter` with `gritmeter/` and `site/` beside them (never `.env` or
`data/`), then `docker compose up -d --build` and prints `docker compose ps`, run
in the background if the build is slow (first build on the Pi takes a few minutes). Then check:
```bash
ssh pi4.local 'cd /opt/gritmeter && docker compose ps && docker compose logs --tail 20 gritmeter'
curl -s https://my.gritmeter.app/health      # "ok"
```
Both containers (`gritmeter`, `cloudflared`) must be up. If `host/firewall.sh` changed, the rules only apply after
`ssh -t pi4.local sudo /opt/gritmeter/firewall.sh`, which needs the user's password: give them the command to run.

## 4. Report
Which target(s) went out, the commit deployed (`git log --oneline -1`), what the checks showed, and anything not done
(push, firewall rerun).
