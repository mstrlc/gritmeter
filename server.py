#!/usr/bin/env python3
"""gritmeter backend for my.gritmeter.app: Strava webhook, sign-in, the rider's pages, and a worker that rates rides.

One process: a threaded HTTP server (answers webhooks in well under Strava's 2 s by only queueing) and one worker
thread that drains the SQLite job queue. Strava only drops events while we are unreachable, so a heartbeat thread
checks the public URL and the worker runs a catch-up pass after each outage (one call per rider), never on a timer.
Every Strava call for a ride goes through the worker, so rate limits are handled in one place.

Routes:
  GET  /                      rides (signed in) or the sign-in page
  GET  /settings              settings;  POST /settings saves them
  GET  /login                 redirect to Strava's consent page;  GET /callback is the OAuth return (signs in)
  POST /logout, /disconnect   sign out; revoke access on Strava and delete the athlete's data
  POST /activity/<id>/<add|remove|retry>   queue a rider's request for one ride
  POST /backfill              rate (not write) the last 14 days of rides
  GET  /webhook               Strava subscription validation;  POST /webhook receives events
  GET  /strava/<file>.svg     official Strava logos (from site/strava/)
  GET  /health                "ok"

Usage:
  python3 server.py              # listen on HOST:PORT (default 127.0.0.1:8722); PUBLIC_URL is this site's address
  python3 server.py --dry-run    # rate rides but never write descriptions (status "dryrun")
"""
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store
import strava
import web

HOST = os.environ.get("HOST", "127.0.0.1")  # cloudflared runs on the same machine
PORT = int(os.environ.get("PORT", 8722))
PROBE_EVERY = 120          # seconds between heartbeat checks of PUBLIC_URL/health
CATCHUP_DAYS = 3           # catch-up looks this far back before an outage began (late uploads of earlier rides)
BACKFILL_DAYS = 14         # "rate my last 14 days" on the rides page
MAX_ATTEMPTS = 6           # then the job is marked failed
NEW_DELAY = 180            # wait after an upload so other description apps (weather, Wandrer...) write first
VERIFY_AFTER = 900         # one check this long after writing; restores our block if another app replaced it
IDLE_POLL = 60             # worker re-checks the queue at least this often
STATE_TTL = 600            # seconds an OAuth state stays valid
SESSION_DAYS = 30
COOKIE = "gm_session"
ACTIONS = {"add": "write", "remove": "remove", "retry": None}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' https://gritmeter.app data:; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")

STRAVA_ASSETS_DIR = store.sm.ROOT / "site" / "strava"
STRAVA_ASSETS = {p.name for p in STRAVA_ASSETS_DIR.glob("*.svg")}

wake = threading.Event()   # set when work is queued so the worker starts at once
oauth_states = {}          # state -> created time (single process, so memory is enough)
maps = queue.Queue()       # (job, tiles) for rides in areas with no cached map data yet


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def new_authorize_url():
    """Strava's consent URL with a fresh one-time state (the Connect button must link there directly)."""
    now = time.time()
    for s, t in list(oauth_states.items()):
        if now - t > STATE_TTL:
            del oauth_states[s]
    state = secrets.token_urlsafe(16)
    oauth_states[state] = now
    return strava.authorize_url(public_url() + "/callback", state)


def public_url():
    return os.environ.get("PUBLIC_URL", f"http://localhost:{PORT}").rstrip("/")


# ---------------------------------------------------------------- sessions (signed cookie, no server state)

def _sign(value):
    return hmac.new(os.environ["SESSION_SECRET"].encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session(athlete_id):
    value = f"{athlete_id}.{int(time.time() + SESSION_DAYS * 86400)}"
    return f"{value}.{_sign(value)}"


def read_session(cookie_value):
    """Athlete id from a valid, unexpired session cookie, or None."""
    try:
        aid, exp, sig = cookie_value.split(".")
        if hmac.compare_digest(sig, _sign(f"{aid}.{exp}")) and int(exp) > time.time():
            return int(aid)
    except (ValueError, AttributeError):
        pass
    return None


def csrf_token(cookie_value):
    return _sign("csrf." + cookie_value)[:32]


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "gritmeter"

    # -- plumbing

    def cookie(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    def athlete(self):
        """The signed-in athlete's row, or None (also None if they disconnected since)."""
        aid = read_session(self.cookie())
        return store.get_athlete(aid) if aid else None

    def reply(self, code, body, ctype="text/plain; charset=utf-8", headers=()):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def asset(self, name):
        """Official Strava logos, served from site/strava/ (the same files the landing page uses)."""
        if name not in STRAVA_ASSETS:
            return self.reply(404, "not found")
        data = (STRAVA_ASSETS_DIR / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def page(self, html):
        self.reply(200, html, "text/html; charset=utf-8")

    def redirect(self, location, headers=()):
        self.send_response(303)
        self.send_header("Location", location)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()

    def set_cookie(self, value, max_age):
        secure = "; Secure" if public_url().startswith("https://") else ""
        return ("Set-Cookie", f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}")

    def form(self):
        length = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(min(length, 16384)).decode()).items()}

    def log_message(self, fmt, *args):
        pass  # default logging prints query strings, which carry OAuth codes and the verify token

    # -- GET

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        n = q.get("n", "")
        if url.path == "/health":
            return self.reply(200, "ok")
        if url.path == "/webhook":
            if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == os.environ["STRAVA_VERIFY_TOKEN"]:
                return self.reply(200, json.dumps({"hub.challenge": q.get("hub.challenge", "")}), "application/json")
            return self.reply(403, "forbidden")
        if url.path == "/login":
            return self.login()
        if url.path == "/callback":
            return self.callback(q)
        if url.path.startswith("/strava/"):
            return self.asset(url.path.removeprefix("/strava/"))

        athlete = self.athlete()
        if url.path == "/":
            if not athlete:
                return self.page(web.login_page(new_authorize_url(), n))
            return self.page(web.activities_page(athlete, store.list_jobs(athlete["id"]), csrf_token(self.cookie()), n))
        if url.path == "/settings":
            if not athlete:
                return self.redirect("/")
            return self.page(web.settings_page(athlete, csrf_token(self.cookie()), n))
        m = re.fullmatch(r"/activity/(\d+)", url.path)
        if m and athlete and (job := store.get_job(athlete["id"], int(m[1]))):
            return self.page(web.ride(job, csrf_token(self.cookie())))  # one ride card, polled by the rides page
        self.reply(404, "not found")

    def login(self):
        self.redirect(new_authorize_url())

    def callback(self, q):
        if q.get("error") or oauth_states.pop(q.get("state", ""), None) is None:
            return self.redirect("/?n=expired")
        try:
            athlete = strava.connect(q["code"], q.get("scope", ""))
        except Exception as e:
            log(f"connect failed: {e}")
            return self.redirect("/?n=failed")
        log(f"signed in athlete {athlete['id']}, scopes {q.get('scope', '')}")
        n = "connected" if "activity:write" in q.get("scope", "") else "noscope"
        self.redirect(f"/?n={n}", [self.set_cookie(make_session(athlete["id"]), SESSION_DAYS * 86400)])

    # -- POST

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/webhook":
            return self.webhook()
        form = self.form()
        athlete = self.athlete()
        # SameSite=Lax already blocks cross-site posts; the token is a second lock
        if not athlete or not hmac.compare_digest(form.get("csrf", ""), csrf_token(self.cookie())):
            return self.redirect("/")
        aid = athlete["id"]

        if path == "/logout":
            return self.redirect("/?n=out", [self.set_cookie("", 0)])
        if path == "/disconnect":
            try:
                strava.deauthorize(strava.access_token(aid))
            except Exception as e:  # delete our side regardless; the rider can also revoke on Strava
                log(f"deauthorize failed for {aid}: {e}")
            store.delete_athlete(aid)
            log(f"athlete {aid} disconnected, data deleted")
            return self.redirect("/?n=gone", [self.set_cookie("", 0)])
        if path == "/settings":
            store.save_settings(aid, **{k: form.get(k) == "1" for k in store.SETTINGS})
            store.save_sport_types(aid, [t for t in strava.RIDE_TYPES if form.get(f"type_{t}") == "1"])
            return self.redirect("/settings?n=saved")
        if path == "/backfill":
            try:
                ids = strava.recent_activity_ids(strava.access_token(aid), time.time() - BACKFILL_DAYS * 86400)
            except Exception as e:
                log(f"backfill failed for {aid}: {e}")
                return self.redirect("/?n=failed")
            queued = sum(store.enqueue(aid, i, action="rate") for i in ids)
            log(f"backfill: queued {queued} for athlete {aid}")
            wake.set()
            return self.redirect("/?n=backfill")
        m = re.fullmatch(r"/activity/(\d+)/(add|remove|retry)", path)
        if m and store.request_action(aid, int(m[1]), ACTIONS[m[2]]):
            wake.set()
            if self.headers.get("X-Fetch"):  # the rides page swaps in the updated card itself
                return self.page(web.ride(store.get_job(aid, int(m[1])), csrf_token(self.cookie())))
            return self.redirect(f"/#ride-{m[1]}")  # without JS: back to the ride, whose status shows the request
        self.reply(404, "not found")

    def webhook(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            handle_event(json.loads(self.rfile.read(min(length, 16384))))
        except Exception as e:  # always 200 for well-formed requests; Strava retries otherwise
            log(f"bad webhook event: {e}")
        self.reply(200, "ok")


def handle_event(e):
    """Queue new activities; forget deleted activities and deauthorized athletes. Ignores everything else
    (including activity updates, which our own description edits trigger)."""
    owner, obj = int(e["owner_id"]), int(e["object_id"])
    if e["object_type"] == "athlete" and e.get("updates", {}).get("authorized") == "false":
        store.delete_athlete(owner)
        log(f"athlete {owner} deauthorized, data deleted")
    elif e["object_type"] == "activity" and e["aspect_type"] == "delete":
        store.delete_job(obj)
    elif e["object_type"] == "activity" and e["aspect_type"] == "create":
        if not store.get_athlete(owner):
            return log(f"event for unknown athlete {owner}, ignored")
        if store.enqueue(owner, obj, delay=NEW_DELAY):
            log(f"queued {obj} (athlete {owner}), due in {NEW_DELAY // 60} min")


# ---------------------------------------------------------------- worker

def heartbeat():
    """Check every PROBE_EVERY s that Strava can reach us (through the tunnel). Records "alive_at", and "outage_since"
    when a check fails; a restart after a gap counts as an outage too. The worker catches up once we're back."""
    alive = store.get_state("alive_at")
    if alive and time.time() - alive > 2 * PROBE_EVERY and store.get_state("outage_since") is None:
        store.set_state("outage_since", alive)
        log(f"down since {time.strftime('%Y-%m-%d %H:%M', time.localtime(alive))}, will catch up")
    while True:
        try:
            # Cloudflare answers 403 to Python's default User-Agent
            req = urllib.request.Request(public_url() + "/health",
                                         headers={"User-Agent": "gritmeter-heartbeat/1.0 (+https://gritmeter.app)"})
            urllib.request.urlopen(req, timeout=15).read()
            store.set_state("alive_at", time.time())
        except Exception as e:
            if store.get_state("outage_since") is None:
                store.set_state("outage_since", store.get_state("alive_at") or time.time())
                log(f"unreachable at {public_url()} ({e}), will catch up once back")
        time.sleep(PROBE_EVERY)


def catch_up_due():
    """Start of the outage to catch up on, once we're reachable again; else None."""
    since, alive = store.get_state("outage_since"), store.get_state("alive_at")
    return since if since is not None and alive is not None and alive > since + PROBE_EVERY else None


def catch_up(since):
    """Queue activities Strava couldn't deliver while we were unreachable: one call per rider, per outage.
    Only rides uploaded since the athlete connected: older ones are left to the rider (backfill).
    Resumable: a rate-limit pause continues after the last rider done ("catchup_done")."""
    done = store.get_state("catchup_done") or 0
    for aid in store.athlete_ids():
        if aid <= done:
            continue
        if strava.wait_for_rate_limit():  # the worker pauses, then resumes after `done`
            raise strava.RateLimited("near the limit during catch-up")
        try:
            a = store.get_athlete(aid)
            after = max(since - CATCHUP_DAYS * 86400, a["connected_at"] or 0)
            new = [i for i in strava.recent_activity_ids(strava.access_token(aid), after)
                   if store.enqueue(aid, i, delay=NEW_DELAY)]
            if new:
                log(f"catch-up: queued {len(new)} for athlete {aid}")
        except strava.RateLimited:
            raise
        except Exception as e:
            log(f"catch-up failed for athlete {aid}: {e}")
        store.set_state("catchup_done", aid)
    store.set_state("catchup_done", None)
    if store.get_state("outage_since") == since:  # unless a new outage began meanwhile
        store.set_state("outage_since", None)
    log("catch-up done")


def run_job(job, write):
    aid, act = job["athlete_id"], job["activity_id"]
    athlete = store.get_athlete(aid)
    if not athlete:
        return store.finish(act, "failed", "athlete not connected")
    try:
        p = strava.process(act, strava.access_token(aid), write, bool(athlete["auto_write"]),
                           bool(athlete["skip_private"]), job["action"], store.sport_types(athlete),
                           bool(athlete["skip_commute"]), map_wait=True)
    except strava.RateLimited:
        raise
    except Exception as e:
        return failed(job, e)
    if p["status"] == "mapping":
        store.wait_for_map(act, p["name"], p["start_date"], p["sport_type"])
        maps.put((job, p["tiles"]))
        return log(f"{act}: new area, fetching {len(p['tiles'])} map tiles")
    verify_at = time.time() + VERIFY_AFTER if p["detail"] == "written" else None
    store.finish(act, p["status"], p["detail"], p["result"], p["name"], p["start_date"], verify_at, p["sport_type"])
    score = f" score {p['result']['score']}" if p["result"] else ""
    log(f"{act}: {p['status']} ({p['detail']}){score}")


def failed(job, e):
    """Requeue a job after an error, backing off (1 min, 4, 16, ... up to 6 h) until MAX_ATTEMPTS."""
    act = job["activity_id"]
    delay = min(60 * 4 ** job["attempts"], 6 * 3600)
    store.retry(act, f"{type(e).__name__}: {e}", delay, MAX_ATTEMPTS)
    log(f"{act}: attempt {job['attempts'] + 1} failed ({e}), retry in {delay // 60} min")
    if not isinstance(e, RuntimeError):
        traceback.print_exc()


def map_fetcher():
    """Fetch map tiles for rides in new areas, one tile at a time (Overpass), so the worker keeps rating rides
    elsewhere. Every tile is tried before giving up, so each retry of the ride only has the failed ones left."""
    while True:
        job, tiles = maps.get()
        try:
            bad = []
            for t in tiles:
                try:
                    store.sm.fetch_tile(*t)
                except Exception as e:
                    bad.append(e)
            if bad:
                failed(job, RuntimeError(f"map data for {len(bad)} of {len(tiles)} tiles failed: {bad[0]}"))
            else:
                store.map_ready(job["activity_id"])
                log(f"{job['activity_id']}: map tiles cached, rating")
                wake.set()
        except Exception:
            traceback.print_exc()


def run_verify(job):
    """The one follow-up check of a written ride; a failure is logged and not retried."""
    act = job["activity_id"]
    try:
        outcome = strava.restore_block(act, strava.access_token(job["athlete_id"]), job["result"])
    except strava.RateLimited:
        raise
    except Exception as e:
        outcome = None
        log(f"{act}: follow-up check failed ({e})")
    store.verified(act, "restored" if outcome == "restored" else None)
    if outcome == "restored":
        log(f"{act}: our block was gone after another edit, restored")


def worker(write):
    while True:
        try:
            pause = strava.wait_for_rate_limit()
            if pause:
                log(f"rate limit near {strava.usage}, pausing {pause / 60:.0f} min")
                time.sleep(pause)
                strava.usage.clear()
            if (since := catch_up_due()) is not None:
                catch_up(since)
            job = store.next_job()
            check = store.next_verify() if write and not job else None
            if job:
                run_job(job, write)
            elif check:
                run_verify(check)
            else:
                wake.wait(IDLE_POLL)
                wake.clear()
        except strava.RateLimited as e:
            log(f"429 from Strava ({e}), pausing until the next 15-minute window")
            time.sleep(900 - time.time() % 900 + 5)
        except Exception:
            traceback.print_exc()
            time.sleep(IDLE_POLL)


def main():
    strava.load_env("STRAVA_VERIFY_TOKEN", "SESSION_SECRET")
    write = "--dry-run" not in sys.argv
    web.WRITING = write
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)  # bind first: if the port is taken, no worker starts
    if n := store.requeue("mapping"):  # their tile fetches were in memory only
        log(f"requeued {n} rides waiting for map data")
    threading.Thread(target=worker, args=(write,), daemon=True, name="worker").start()
    threading.Thread(target=map_fetcher, daemon=True, name="maps").start()
    threading.Thread(target=heartbeat, daemon=True, name="heartbeat").start()
    log(f"listening on {HOST}:{PORT}, public URL {public_url()}{'' if write else ', DRY RUN (no descriptions written)'}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
