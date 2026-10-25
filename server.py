#!/usr/bin/env python3
"""gritmeter backend for my.gritmeter.app: Strava webhook, sign-in, the rider's pages, and a worker that rates rides.

One process: a threaded HTTP server (answers webhooks in well under Strava's 2 s by only queueing) and one worker
thread that drains the SQLite job queue and runs a catch-up pass every few hours for events Strava dropped.
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
import re
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store
import strava
import web

HOST = os.environ.get("HOST", "127.0.0.1")  # cloudflared runs on the same machine
PORT = int(os.environ.get("PORT", 8722))
CATCHUP_EVERY = 6 * 3600   # seconds between catch-up passes
CATCHUP_DAYS = 3           # how far back a catch-up pass looks (never before the athlete connected)
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
            return self.redirect(f"/#ride-{m[1]}")  # back to the ride, whose status shows the request
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

def catch_up():
    """Queue recent activities we haven't seen (Strava drops events after a few retries).
    Only rides uploaded since the athlete connected: older ones are left to the rider (backfill)."""
    for aid in store.athlete_ids():
        try:
            a = store.get_athlete(aid)
            after = max(time.time() - CATCHUP_DAYS * 86400, a["connected_at"] or 0)
            new = [i for i in strava.recent_activity_ids(strava.access_token(aid), after)
                   if store.enqueue(aid, i, delay=NEW_DELAY)]
            if new:
                log(f"catch-up: queued {len(new)} for athlete {aid}")
        except strava.RateLimited:
            raise
        except Exception as e:
            log(f"catch-up failed for athlete {aid}: {e}")


def run_job(job, write):
    aid, act = job["athlete_id"], job["activity_id"]
    athlete = store.get_athlete(aid)
    if not athlete:
        return store.finish(act, "failed", "athlete not connected")
    try:
        p = strava.process(act, strava.access_token(aid), write, bool(athlete["auto_write"]),
                           bool(athlete["skip_private"]), job["action"], store.sport_types(athlete),
                           bool(athlete["skip_commute"]))
    except strava.RateLimited:
        raise
    except Exception as e:
        delay = min(60 * 4 ** job["attempts"], 6 * 3600)
        store.retry(act, f"{type(e).__name__}: {e}", delay, MAX_ATTEMPTS)
        log(f"{act}: attempt {job['attempts'] + 1} failed ({e}), retry in {delay // 60} min")
        if not isinstance(e, RuntimeError):
            traceback.print_exc()
        return
    verify_at = time.time() + VERIFY_AFTER if p["detail"] == "written" else None
    store.finish(act, p["status"], p["detail"], p["result"], p["name"], p["start_date"], verify_at, p["sport_type"])
    score = f" score {p['result']['score']}" if p["result"] else ""
    log(f"{act}: {p['status']} ({p['detail']}){score}")


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
    next_catchup = 0
    while True:
        try:
            pause = strava.wait_for_rate_limit()
            if pause:
                log(f"rate limit near {strava.usage}, pausing {pause / 60:.0f} min")
                time.sleep(pause)
                strava.usage.clear()
            if time.time() >= next_catchup:
                catch_up()
                next_catchup = time.time() + CATCHUP_EVERY
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
    threading.Thread(target=worker, args=(write,), daemon=True, name="worker").start()
    log(f"listening on {HOST}:{PORT}, public URL {public_url()}{'' if write else ', DRY RUN (no descriptions written)'}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
