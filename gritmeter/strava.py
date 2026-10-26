#!/usr/bin/env python3
"""Strava client: OAuth, token refresh, streams fetch, the description writer and webhook subscriptions.

Credentials come from the environment or an untracked `.env` (STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, and
STRAVA_VERIFY_TOKEN for webhooks). Tokens live in the SQLite store (store.py) and are never printed.

Usage:
  python3 strava.py auth                         # connect an athlete (opens the browser, callback on localhost)
  python3 strava.py rate <activity_id>           # fetch streams, rate, print the description block
  python3 strava.py rate <activity_id> --write   # ...and append/replace our block in the activity description
  python3 strava.py subscribe <callback_url>     # register the webhook (server.py must be reachable there)
  python3 strava.py subscriptions                # show the app's webhook subscription
  python3 strava.py unsubscribe <id>             # delete it
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import ride_rating as rr
import store

API = "https://www.strava.com/api/v3"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
SCOPES = "read,activity:read_all,activity:write"
CALLBACK_PORT = 8721  # Strava always allows localhost as a callback domain
RIDE_TYPES = {"Ride", "GravelRide", "MountainBikeRide", "EBikeRide", "EMountainBikeRide"}
RATE_HEADROOM = 0.9   # pause the worker once any rate-limit window is 90% used
MIN_KM = 0.2          # shorter GPS tracks (a recording started by mistake) aren't rated
MAX_UNKNOWN_PCT = 50  # rides mostly off mapped roads get no block: a score from the rest would mislead

usage = {}  # latest rate-limit usage from response headers: {"15min": (used, limit), "daily": (used, limit)}


class RateLimited(RuntimeError):
    """Strava answered 429; the caller should wait (see wait_for_rate_limit) and retry."""


# ---------------------------------------------------------------- config

def load_env(*extra):
    """Read KEY=VALUE lines from .env into os.environ (existing variables win); exit if a required key is missing."""
    path = store.sm.ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    missing = [k for k in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", *extra) if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing {', '.join(missing)} (set them in the environment or .env)")


def client_params():
    return {"client_id": os.environ["STRAVA_CLIENT_ID"], "client_secret": os.environ["STRAVA_CLIENT_SECRET"]}


# ---------------------------------------------------------------- HTTP + rate limits

def request(method, url, token=None, data=None):
    """JSON request; form-encodes `data`. Records rate-limit usage. Raises with Strava's error body on HTTP errors."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            _record_usage(r.headers)
            return json.load(r) if r.status != 204 else None
    except urllib.error.HTTPError as e:
        _record_usage(e.headers)
        msg = f"{method} {url.split('?')[0]} -> {e.code}: {e.read().decode()[:300]}"
        raise (RateLimited if e.code == 429 else RuntimeError)(msg) from None


def _record_usage(headers):
    """Replace `usage` with this response's readings, keeping the tighter of the overall and read-only limits."""
    latest = {}
    for prefix in ("X-RateLimit", "X-ReadRateLimit"):
        limit, used = headers.get(f"{prefix}-Limit"), headers.get(f"{prefix}-Usage")
        if limit and used:
            for window, u, lim in zip(("15min", "daily"), used.split(","), limit.split(",")):
                u, lim = int(u), int(lim)
                if window not in latest or u / lim > latest[window][0] / latest[window][1]:
                    latest[window] = (u, lim)
    if latest:
        usage.clear()
        usage.update(latest)


def wait_for_rate_limit():
    """Seconds to pause before the next request (0 if there is room). Windows reset at :00/:15/:30/:45 and 00:00 UTC."""
    now = time.time()
    if "daily" in usage and usage["daily"][0] >= RATE_HEADROOM * usage["daily"][1]:
        return 86400 - now % 86400 + 5
    if "15min" in usage and usage["15min"][0] >= RATE_HEADROOM * usage["15min"][1]:
        return 900 - now % 900 + 5
    return 0


# ---------------------------------------------------------------- OAuth

def authorize_url(redirect_uri, state):
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": os.environ["STRAVA_CLIENT_ID"], "redirect_uri": redirect_uri, "response_type": "code",
        "approval_prompt": "auto", "scope": SCOPES, "state": state})


def connect(code, granted_scope):
    """Exchange an authorization code for tokens and store the athlete. Returns the Strava athlete dict."""
    t = request("POST", TOKEN_URL, data={**client_params(), "grant_type": "authorization_code", "code": code})
    athlete = t["athlete"]
    store.save_athlete(athlete["id"], athlete.get("firstname"), t | {"scope": granted_scope})
    return athlete


def auth():
    """Connect one athlete from the command line: browser consent -> localhost callback -> store tokens."""
    state = secrets.token_urlsafe(16)
    url = authorize_url(f"http://localhost:{CALLBACK_PORT}/callback", state)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            result.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"gritmeter: done, you can close this tab.")

        def log_message(self, *args):
            pass  # the query string holds the auth code

    print(f"opening Strava consent page (if it doesn't open, visit):\n{url}")
    webbrowser.open(url)
    server = HTTPServer(("localhost", CALLBACK_PORT), Callback)
    while "code" not in result and "error" not in result:
        server.handle_request()
    if "error" in result or result.get("state", [""])[0] != state:
        sys.exit(f"authorization failed: {result.get('error', ['state mismatch'])[0]}")

    granted = result.get("scope", [""])[0]
    athlete = connect(result["code"][0], granted)
    print(f"connected athlete {athlete['id']} ({athlete.get('firstname', '')}), scopes: {granted}")
    if "activity:write" not in granted:
        print("note: activity:write not granted, descriptions can't be updated")


def access_token(athlete_id=None):
    """A valid access token for the athlete (the first connected one if not given), refreshing if near expiry."""
    t = store.get_athlete(athlete_id)
    if not t:
        raise RuntimeError(f"athlete {athlete_id} not connected" if athlete_id else
                           "no athlete connected; run: python3 strava.py auth")
    if t["expires_at"] < time.time() + 300:
        new = request("POST", TOKEN_URL, data={**client_params(), "grant_type": "refresh_token",
                                                "refresh_token": t["refresh_token"]})
        store.save_athlete(t["id"], None, new)
        return new["access_token"]
    return t["access_token"]


# ---------------------------------------------------------------- activities

def fetch_points(activity_id, token):
    """Full-resolution [(lat, lon)] from the streams API, or None for activities without GPS."""
    streams = request("GET", f"{API}/activities/{activity_id}/streams?keys=latlng&key_by_type=true", token)
    latlng = streams.get("latlng", {}).get("data") if isinstance(streams, dict) else None
    return [tuple(p) for p in latlng] if latlng else None


def recent_activity_ids(token, after):
    """Ids of the athlete's activities that started after the unix time `after` (one page, newest first)."""
    after = int(after)
    return [a["id"] for a in request("GET", f"{API}/athlete/activities?after={after}&per_page=50", token)]


def process(activity_id, token, write=True, auto_write=True, skip_private=False, action=None, write_types=None,
            skip_commute=False, map_wait=False):
    """Rate one activity and put our block in its description, following the rider's settings.

    write=False is a test run (never PUTs). action is the rider's explicit request: "write" (ignore auto_write,
    skip_private and skip_commute), "remove" (take our block out) or "rate" (rate only: past rides). write_types limits automatic
    writing to those sport_types (None = all rides). Returns a dict: status ("done", "rated", "dryrun", "removed",
    "skipped"), detail, result (rate() output or None), name, start_date, sport_type. About 4 API calls per written ride.
    map_wait=True returns status "mapping" with the uncached "tiles" instead of fetching them from Overpass inline.
    """
    act = request("GET", f"{API}/activities/{activity_id}", token)
    out = {"name": act.get("name"), "start_date": act.get("start_date_local"),
           "sport_type": act.get("sport_type"), "result": None}
    current = act.get("description") or ""

    if action == "remove":
        new = rr.strip_block(current)  # no slow step before the PUT, so this read is fresh enough
        if write and new != current:
            request("PUT", f"{API}/activities/{activity_id}", token, data={"description": new})
        return out | {"status": "removed" if write else "dryrun", "detail": "removed" if new != current else "not there"}
    if act.get("sport_type") not in RIDE_TYPES:
        return out | {"status": "skipped", "detail": f"sport_type {act.get('sport_type')}"}
    if act.get("trainer") or act.get("manual"):
        return out | {"status": "skipped", "detail": "indoor or manual"}
    points = fetch_points(activity_id, token)
    if not points:
        return out | {"status": "skipped", "detail": "no GPS stream"}
    if map_wait and (missing := rr.sm.missing_tiles(points)):
        return out | {"status": "mapping", "detail": "new area", "tiles": missing}
    r = rr.rate(points)
    if r["km"] < MIN_KM:
        return out | {"status": "skipped", "detail": "too short"}
    if r["pct"]["unknown"] > MAX_UNKNOWN_PCT:
        return out | {"status": "skipped", "detail": "not enough map data"}
    out["result"] = r

    if not write:
        return out | {"status": "dryrun", "detail": "test run"}
    if action == "rate":
        return out | {"status": "rated", "detail": "past ride"}
    if action != "write":
        if not auto_write:
            return out | {"status": "rated", "detail": "auto-add off"}
        if skip_private and act.get("visibility") == "only_me":
            return out | {"status": "rated", "detail": "private ride"}
        if skip_commute and act.get("commute"):
            return out | {"status": "rated", "detail": "commute"}
        if write_types is not None and act["sport_type"] not in write_types:
            return out | {"status": "rated", "detail": f"type off {act['sport_type']}"}
    return out | {"status": "done", "detail": write_block(activity_id, token, r)}


def write_block(activity_id, token, r):
    """Put our block for rating r into the description; returns "written" or "unchanged".

    Strava has no conditional update, so another app writing between our read and our write loses its text (or
    ours). Reading right before the PUT, after the slow rating step, keeps that window to one round trip.
    """
    current = request("GET", f"{API}/activities/{activity_id}", token).get("description") or ""
    new = rr.merge_description(current, rr.description_block(r))
    if new == current:
        return "unchanged"
    request("PUT", f"{API}/activities/{activity_id}", token, data={"description": new})
    return "written"


def restore_block(activity_id, token, r):
    """Follow-up check after writing: if another app replaced the description and our block is gone, add it back.
    Returns "restored" or "still there"."""
    current = request("GET", f"{API}/activities/{activity_id}", token).get("description") or ""
    if rr.has_block(current):
        return "still there"
    request("PUT", f"{API}/activities/{activity_id}", token,
            data={"description": rr.merge_description(current, rr.description_block(r))})
    return "restored"


def deauthorize(token):
    """Revoke the app's access for this athlete on Strava's side."""
    request("POST", "https://www.strava.com/oauth/deauthorize", data={"access_token": token})


def rate_activity(activity_id, write=False):
    p = process(activity_id, access_token(), write, action="write" if write else None)
    if p["result"]:
        r = p["result"]
        print(f"{activity_id}: {r['km']} km, score {r['score']}\n\n{rr.description_block(r)}\n")
    if p["status"] != "dryrun":
        print(f"{p['status']}: {p['detail']}")


# ---------------------------------------------------------------- webhook subscription (one per app)

def subscribe(callback_url):
    """Register the webhook. Strava immediately GETs callback_url to validate, so server.py must be live there."""
    sub = request("POST", f"{API}/push_subscriptions", data={
        **client_params(), "callback_url": callback_url, "verify_token": os.environ["STRAVA_VERIFY_TOKEN"]})
    print(f"subscribed: id {sub['id']}")


def subscriptions():
    return request("GET", f"{API}/push_subscriptions?" + urllib.parse.urlencode(client_params()))


def unsubscribe(sub_id):
    request("DELETE", f"{API}/push_subscriptions/{sub_id}?" + urllib.parse.urlencode(client_params()))
    print(f"deleted subscription {sub_id}")


def main():
    args = sys.argv[1:]
    load_env(*(["STRAVA_VERIFY_TOKEN"] if args[:1] == ["subscribe"] else []))
    try:
        if args[:1] == ["auth"]:
            auth()
        elif args[:1] == ["rate"] and len(args) >= 2:
            rate_activity(args[1], write="--write" in args)
        elif args[:1] == ["subscribe"] and len(args) == 2:
            subscribe(args[1])
        elif args[:1] == ["subscriptions"]:
            print(json.dumps(subscriptions(), indent=1))
        elif args[:1] == ["unsubscribe"] and len(args) == 2:
            unsubscribe(args[1])
        else:
            sys.exit(__doc__)
    except RuntimeError as e:
        hint = " (not a Strava activity id of the connected athlete? Garmin ids differ)" if "404" in str(e) else ""
        sys.exit(f"{e}{hint}")


if __name__ == "__main__":
    main()
