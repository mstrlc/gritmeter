# Developing and releasing gritmeter

`main` is what is running. Every push to `main` deploys gritmeter.app and my.gritmeter.app and tags the commit as a
GitHub release, so the latest release is always the live code. Day-to-day work happens on `develop`.

```
feat/<thing> ──PR──> develop ──release PR (bumps VERSION)──> main ──> deploy web + Pi ──> release vX.Y.Z
                                                              ^
hotfix/<thing> ──PR (patch bump)──────────────────────────────┘  then merge main back into develop
```

## Branches

| Branch | What it is | How changes get in |
|---|---|---|
| `main` | the running code; one merge commit per release | release PRs from `develop`, or `hotfix/*` |
| `develop` | the next release | PRs from `feat/*`, `fix/*`, `chore/*`, `docs/*` |
| `feat/<thing>` etc. | one change each | push freely; branch from `develop` |

Both `main` and `develop` are protected by rulesets: no direct pushes, no force pushes, no deletion, and a PR needs
its checks to pass (no approval is required). `main` only accepts merge commits.

Commit messages and PR titles use conventional commits: `feat(server): ...`, `fix(rating): ...`, `chore: ...`,
`docs: ...`, `ci: ...`. Release notes are generated from the PR titles, so make them read well.

## Working on a change

```bash
git switch develop && git pull
git switch -c feat/<thing>
# ... edit, run, commit ...
git push -u origin feat/<thing>
gh pr create --base develop
```

There is no test suite yet. Before opening the PR, run what you touched: `python3 gritmeter/ride_rating.py` for
classifier or snapping changes (compare with `data/ratings.txt`), `python3 gritmeter/server.py --dry-run` for the
app, and open `site/index.html` for the landing page. [`AGENTS.md`](AGENTS.md) has the conventions to keep.

CI (`.github/workflows/ci.yml`, job `check`) runs on every PR into `develop` or `main`: the Python compiles, the shell
scripts in `host/` parse, and `host/compose.yaml` is valid. Squash or merge into `develop`, either is fine.

## Releasing

1. Decide the version. While gritmeter is capped and in testing it stays on `0.x`: bump the minor
   (`0.1.0` -> `0.2.0`) for a release with features, the patch (`0.2.0` -> `0.2.1`) for fixes only. `1.0.0` is for
   opening to everyone.
2. On a branch from `develop`, set `VERSION` to it and PR that into `develop` (`chore: release 0.2.0`), or include
   the bump in the last feature PR.
3. Open the release PR from `develop` into `main`, titled `release: v0.2.0`, listing what goes out:
   ```bash
   gh pr create --base main --head develop --title "release: v0.2.0"
   ```
   CI's `release` job checks that the PR comes from `develop` (or `hotfix/*`) and that `VERSION` is valid, newer
   than the latest tag and not released yet.
4. Merge it **with a merge commit** (the only option `main` allows). A squash would leave `develop` behind `main`
   and make the next release PR conflict.
5. `release.yml` runs on the push to `main`:
   - `version` reads `VERSION` and stops if that tag already exists;
   - `web` deploys `site/` with wrangler and checks that gritmeter.app answers;
   - `pi` joins the tailnet, runs `host/deploy.sh` and waits for `my.gritmeter.app/health` to say `ok`;
   - `release` (only if both deploys passed) creates the tag and GitHub release `vX.Y.Z` with generated notes.
6. Check the run (`gh run watch`) and the release (`gh release view`).

The Pi deploy rebuilds the image on the Pi and restarts the app only if something in it changed. The Pi is live
(`SERVER_FLAGS=` empty in its `.env`), so a restarted app rates rides and writes real descriptions as soon as it is
up. The workflow never changes `SERVER_FLAGS`.

### Hotfixes

For something that cannot wait for `develop`: branch `hotfix/<thing>` from `main`, fix it, bump the patch in
`VERSION`, PR into `main` and merge. Then bring the fix into `develop` with a PR from `main` into `develop`.

### When a release run fails

- Nothing is tagged unless both deploys pass, so the failed release can simply be retried:
  `gh run rerun <run-id> --failed` after fixing the cause outside the repo (secrets, Tailscale, the Pi).
- If the fix needs a code change, make it as a hotfix with a new patch version; the half-deployed version is never
  tagged.
- The `web` and `pi` jobs are independent: one target can be live with the new code while the other is not.
  `gh run view <run-id>` shows which.

## Manual deploys

Only for new OSM tiles and for emergencies, from a clean `main` that matches `origin/main`. Tiles are not in git, so
they always go from the Mac:

```bash
.venv/bin/python gritmeter/extract_osm.py data/pbf/*.osm.pbf
host/deploy.sh --osm
```

`npx wrangler deploy` and `host/deploy.sh` work as before for emergencies; the `/deploy` skill has the steps.

## How the deploy reaches the Pi

The repository is public, so the Pi has no GitHub runner on it and no SSH port open to the internet. Instead:

- The `pi` job joins the tailnet as a temporary device tagged `tag:ci`, using Tailscale workload identity
  federation: GitHub's OIDC token is exchanged for a Tailscale key, so there is no Tailscale secret to leak or rotate.
- The tailnet policy lets `tag:ci` reach only `100.78.229.132:22` (the Pi).
- It SSHes in as `matyas` with a deploy key used only by CI. On the Pi that key is limited to tailnet addresses
  (`from="100.64.0.0/10"`) with forwarding off; `.github/pi_known_hosts` pins the Pi's host key.
- From there it is the same `host/deploy.sh` as a manual deploy: rsync the code, `docker compose up -d --build`.

### Secrets and settings

All in the GitHub environment `production`, which only `main` can deploy from.

| Name | Kind | What it is |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | secret | Cloudflare token from the "Edit Cloudflare Workers" template |
| `CLOUDFLARE_ACCOUNT_ID` | secret | the Cloudflare account (`npx wrangler whoami`) |
| `TS_OAUTH_CLIENT_ID` | secret | client ID of the Tailscale trust credential |
| `TS_AUDIENCE` | secret | audience of the same credential |
| `PI_SSH_KEY` | secret | private half of the CI deploy key |
| `PI_HOST` | variable | the Pi's tailnet IP, `100.78.229.132` |

The Tailscale trust credential (admin console, Settings → Trust credentials) uses issuer
`https://token.actions.githubusercontent.com`, scope `auth_keys` (write), tag `tag:ci`, and subject
`repo:mstrlc@44923567/gritmeter@1432591707:environment:production`. The repository uses GitHub's immutable
subjects (owner and repo by numeric id), so the plain `repo:mstrlc/gritmeter:...` form is rejected with a 403.

To replace the deploy key: generate a new ed25519 key, add its public half to `~/.ssh/authorized_keys` on the Pi with
the same `from=...,no-port-forwarding,no-agent-forwarding,no-X11-forwarding` prefix, store the private half with
`gh secret set PI_SSH_KEY --env production`, then remove the old line from `authorized_keys`.
