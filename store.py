"""SQLite storage for connected athletes, their settings and the activity job queue.

Kept behind these few functions so Postgres can replace it later. One connection per thread (WAL mode).
Tokens are stored in plain text; the file is created mode 600 and lives in the untracked data/ directory.
"""
import json
import os
import sqlite3
import threading
import time

import surface_mix as sm

DB_PATH = sm.ROOT / "data" / "gritmeter.db"
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS athletes (
    id INTEGER PRIMARY KEY,
    firstname TEXT,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    scope TEXT,
    connected_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    activity_id INTEGER PRIMARY KEY,   -- dedupe: Strava retries webhooks, catch-up re-lists activities
    athlete_id INTEGER NOT NULL,
    status TEXT NOT NULL,              -- queued | done | rated | dryrun | removed | skipped | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try REAL NOT NULL,
    detail TEXT,                       -- skip reason, "written"/"unchanged", or last error
    result TEXT,                       -- rate() output as JSON
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs (status, next_try);
"""

# columns added after the first schema: (table, column, definition)
MIGRATIONS = [
    ("athletes", "auto_write", "INTEGER NOT NULL DEFAULT 1"),    # add our block to new rides without asking
    ("athletes", "skip_private", "INTEGER NOT NULL DEFAULT 0"),  # rate "only you" rides but leave them unwritten
    ("jobs", "name", "TEXT"),
    ("jobs", "start_date", "TEXT"),                               # local start time, ISO
    ("jobs", "action", "TEXT"),                                   # rider's request: NULL (follow settings) | write | remove
]

SETTINGS = ("auto_write", "skip_private")


def db():
    """This thread's connection, creating or migrating the database on first use."""
    if not hasattr(_local, "conn"):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not DB_PATH.exists():
            os.close(os.open(DB_PATH, os.O_CREAT | os.O_WRONLY, 0o600))
        conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        for table, col, definition in MIGRATIONS:
            if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS jobs_athlete ON jobs (athlete_id, start_date)")
        _local.conn = conn
        _import_json_tokens(conn)
    return _local.conn


def _import_json_tokens(conn):
    """One-time move of tokens from the old data/strava_tokens.json store."""
    old = sm.ROOT / "data" / "strava_tokens.json"
    if old.exists() and not conn.execute("SELECT 1 FROM athletes").fetchone():
        for aid, t in json.loads(old.read_text()).items():
            save_athlete(int(aid), None, t, conn)


# ---------------------------------------------------------------- athletes

def save_athlete(athlete_id, firstname, t, conn=None):
    """Insert or update an athlete's tokens (t: access_token, refresh_token, expires_at[, scope])."""
    (conn or db()).execute(
        """INSERT INTO athletes (id, firstname, access_token, refresh_token, expires_at, scope, connected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET firstname = COALESCE(excluded.firstname, firstname),
             access_token = excluded.access_token, refresh_token = excluded.refresh_token,
             expires_at = excluded.expires_at, scope = COALESCE(excluded.scope, scope)""",
        (athlete_id, firstname, t["access_token"], t["refresh_token"], t["expires_at"], t.get("scope"), time.time()))


def get_athlete(athlete_id=None):
    """One athlete's row as a dict (the first connected one if no id), or None."""
    if athlete_id is None:
        row = db().execute("SELECT * FROM athletes ORDER BY connected_at LIMIT 1").fetchone()
    else:
        row = db().execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
    return dict(row) if row else None


def athlete_ids():
    return [r[0] for r in db().execute("SELECT id FROM athletes")]


def save_settings(athlete_id, **values):
    """Update the athlete's settings (keys from SETTINGS, values 0/1)."""
    for key, value in values.items():
        if key in SETTINGS:
            db().execute(f"UPDATE athletes SET {key} = ? WHERE id = ?", (int(bool(value)), athlete_id))


def delete_athlete(athlete_id):
    """Forget an athlete completely: tokens, settings and every activity record."""
    db().execute("DELETE FROM athletes WHERE id = ?", (athlete_id,))
    db().execute("DELETE FROM jobs WHERE athlete_id = ?", (athlete_id,))


# ---------------------------------------------------------------- jobs

def enqueue(athlete_id, activity_id, action=None):
    """Queue an activity unless it is already known. Returns True if newly queued."""
    now = time.time()
    cur = db().execute("INSERT OR IGNORE INTO jobs (activity_id, athlete_id, status, next_try, created, updated, action) "
                       "VALUES (?, ?, 'queued', ?, ?, ?, ?)", (activity_id, athlete_id, now, now, now, action))
    return cur.rowcount == 1


def request_action(athlete_id, activity_id, action):
    """Requeue one of the athlete's activities with an explicit write/remove request. Returns True if found."""
    cur = db().execute("UPDATE jobs SET status = 'queued', action = ?, attempts = 0, next_try = ?, updated = ? "
                       "WHERE activity_id = ? AND athlete_id = ?",
                       (action, time.time(), time.time(), activity_id, athlete_id))
    return cur.rowcount == 1


def list_jobs(athlete_id, limit=100):
    """The athlete's activities, newest ride first, with result decoded."""
    rows = db().execute("SELECT * FROM jobs WHERE athlete_id = ? ORDER BY COALESCE(start_date, '') DESC, created DESC "
                        "LIMIT ?", (athlete_id, limit))
    return [dict(r) | {"result": json.loads(r["result"]) if r["result"] else None} for r in rows]


def delete_job(activity_id):
    """Drop what we know about an activity the rider deleted."""
    db().execute("DELETE FROM jobs WHERE activity_id = ?", (activity_id,))


def next_job():
    """The oldest due queued job as a dict, or None."""
    row = db().execute("SELECT * FROM jobs WHERE status = 'queued' AND next_try <= ? "
                       "ORDER BY created LIMIT 1", (time.time(),)).fetchone()
    return dict(row) if row else None


def finish(activity_id, status, detail=None, result=None, name=None, start_date=None):
    """Record a processed job; clears any rider action. name/start_date, and the rating after a removal, are kept
    if not given; a skip clears the rating."""
    db().execute("""UPDATE jobs SET status = ?, detail = ?, action = NULL, updated = ?,
                      result = CASE WHEN ? = 'skipped' THEN NULL ELSE COALESCE(?, result) END,
                      name = COALESCE(?, name), start_date = COALESCE(?, start_date) WHERE activity_id = ?""",
                 (status, detail, time.time(), status, json.dumps(result) if result else None, name, start_date,
                  activity_id))


def retry(activity_id, error, delay, max_attempts):
    """Record a failure; requeue after `delay` seconds, or mark failed after max_attempts."""
    db().execute("""UPDATE jobs SET attempts = attempts + 1, detail = ?, updated = ?, next_try = ?,
                      status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'queued' END
                    WHERE activity_id = ?""",
                 (error[:500], time.time(), time.time() + delay, max_attempts, activity_id))


def requeue(status):
    """Put every job with this status back in the queue (e.g. dry-run results before a real run)."""
    return db().execute("UPDATE jobs SET status = 'queued', next_try = ?, attempts = 0 WHERE status = ?",
                        (time.time(), status)).rowcount
