"""HTML for my.gritmeter.app: sign-in, the rider's activities and settings.

Server-rendered with the standard library; styles follow design/identity.html (Epilogue for what we say, DM Mono
for what gritmeter writes and data values; paper/ink neutrals; rough red only for gritmeter's own lines and focus).
Every page credits OpenStreetMap and says Powered by Strava.
"""
import datetime
from html import escape

import ride_rating as rr
import store

# grade labels shown next to the surface mix bar
GRADES = {
    "asphalt": "G0", "paved_rough": "G1", "hardpack": "G2", "gravel": "G3",
    "dirt": "G4", "rough": "G5", "unknown": "G?",
}

# Strava sport_type -> how the settings and rides pages name it (lowercase copy, plural)
RIDE_TYPE_NAMES = {"GravelRide": "gravel rides", "MountainBikeRide": "mountain bike rides", "Ride": "road and other rides",
                   "EBikeRide": "e-bike rides", "EMountainBikeRide": "e-mountain bike rides"}

CSS = """
/* tokens from design/identity.html; the app softens them with rounded corners and card surfaces */
:root {
  --paper: #eef0ea; --paper-2: #e3e6dd; --card: #f8f9f5; --raised: #ffffff; --ink: #13161a; --ink-2: #555c63; --rule: #d3d7cd; --accent: #c42e2a;
  --shadow: 0 1px 2px rgba(19, 22, 26, .06), 0 4px 14px rgba(19, 22, 26, .05);
  --r-sm: 8px; --r: 12px; --r-lg: 18px;
  --display: "Epilogue", "Helvetica Neue", Arial, sans-serif;
  --mono: "DM Mono", ui-monospace, "SF Mono", Menlo, monospace;
  --g0: #3a3f46; --g1: #7487a3; --g2: #d3ab3c; --g3: #dc7429; --g4: #8a522c; --g5: #c42e2a; --gu: #a7aca2;
  --gutter: clamp(16px, 5vw, 64px);
  color-scheme: light;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #0f1214; --paper-2: #1b2023; --card: #161a1d; --raised: #2c3337; --ink: #e7e9e2; --ink-2: #99a09a; --rule: #2a3034; --accent: #ee5a4e;
  --shadow: 0 1px 2px rgba(0, 0, 0, .3); color-scheme: dark;
  --g0: #b4bac1; --g1: #8ea2c2; --g2: #e3c157; --g3: #f08d3d; --g4: #b8794a; --g5: #ee5a4e; --gu: #4c5256;
} }
:root[data-theme="dark"] {
  --paper: #0f1214; --paper-2: #1b2023; --card: #161a1d; --raised: #2c3337; --ink: #e7e9e2; --ink-2: #99a09a; --rule: #2a3034; --accent: #ee5a4e;
  --shadow: 0 1px 2px rgba(0, 0, 0, .3); color-scheme: dark;
  --g0: #b4bac1; --g1: #8ea2c2; --g2: #e3c157; --g3: #f08d3d; --g4: #b8794a; --g5: #ee5a4e; --gu: #4c5256;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 400 1.0625rem/1.6 var(--display); -webkit-font-smoothing: antialiased; }
p, h1, h2, h3, dl, dd, form { margin: 0; }
a { color: inherit; text-underline-offset: 3px; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: var(--r-sm); }
.wrap { max-width: 920px; margin-inline: auto; padding-inline: var(--gutter); }
.mono { font-family: var(--mono); }
.muted { color: var(--ink-2); }
.tnum { font-variant-numeric: tabular-nums; }
.ico { width: 16px; height: 16px; flex: none; }

/* header: sticky, translucent paper */
.top { position: sticky; top: 0; z-index: 10; border-bottom: 1px solid var(--rule);
  background: color-mix(in srgb, var(--paper) 85%, transparent); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.top .wrap { display: flex; align-items: center; gap: 16px; min-height: 68px; flex-wrap: wrap; }
.logo { display: inline-flex; align-items: center; gap: 10px; text-decoration: none; font-weight: 900; letter-spacing: -.06em; font-size: 1.4rem; }
.top nav { margin-left: auto; }
.tools { display: flex; align-items: center; gap: 10px; margin-left: auto; }
.top nav + .tools { margin-left: 0; }
/* segmented controls: a soft track, the current option raised on it */
.tabs, .theme { display: flex; gap: 2px; padding: 3px; background: var(--paper-2); border-radius: var(--r); }
.tabs a, .theme button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; height: 34px; border-radius: var(--r-sm);
  color: var(--ink-2); font: inherit; font-weight: 700; font-size: .9375rem; text-decoration: none; border: 0; background: none; cursor: pointer;
  transition: background .15s, color .15s; }
.tabs a { padding: 0 14px; }
.theme button { width: 34px; padding: 0; }
.theme svg { width: 16px; height: 16px; }
.tabs a:hover, .theme button:hover { color: var(--ink); }
.tabs a[aria-current="page"], .theme button[aria-pressed="true"] { background: var(--raised); color: var(--ink); box-shadow: var(--shadow); }
.linkish { display: inline-flex; align-items: center; gap: 7px; height: 40px; padding: 0 12px; border-radius: var(--r); background: none; border: 0;
  font: inherit; font-weight: 700; font-size: .9375rem; color: var(--ink-2); cursor: pointer; }
.linkish:hover { background: var(--paper-2); color: var(--ink); }
/* phones: logo, sign out and theme on one row; the page tabs full width below */
@media (max-width: 560px) {
  .top .wrap { gap: 10px; padding-block: 10px; min-height: 0; }
  .top .wrap > .tools { margin-left: auto; }
  .top nav { order: 3; flex-basis: 100%; margin-left: 0; }
  .tabs a { flex: 1; }
  .linkish { padding: 0 10px; }
  .linkish .label { display: none; }
}

/* buttons */
.btn { display: inline-flex; align-items: center; gap: 8px; text-decoration: none; font: inherit; font-weight: 800; letter-spacing: -.01em;
  padding: 12px 20px; border-radius: var(--r); background: var(--ink); color: var(--paper); border: 0; cursor: pointer; white-space: nowrap;
  transition: transform .15s, background .15s; }
.btn.sm { padding: 4px 10px; font-size: .8125rem; border-radius: var(--r-sm); gap: 6px; }
.btn.line { background: transparent; color: var(--ink); box-shadow: inset 0 0 0 1.5px var(--rule); }
.btn.line:hover { background: var(--paper-2); }
.btn:hover { transform: translateY(-1px); }
@media (prefers-reduced-motion: reduce) { .btn, .tabs a, .theme button { transition: none; } .btn:hover { transform: none; } }
/* official Strava assets (site/strava/, unmodified): the button links straight to Strava's authorize URL */
.connect { display: inline-block; line-height: 0; }
.connect img { width: 237px; height: 48px; }
.pwrd { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
.pwrd img { height: 14px; width: auto; }
.pwrd .on-dark { display: none; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .pwrd .on-light { display: none; }
  :root:not([data-theme="light"]) .pwrd .on-dark { display: inline; } }
:root[data-theme="dark"] .pwrd .on-light { display: none; }
:root[data-theme="dark"] .pwrd .on-dark { display: inline; }

main { padding-block: clamp(28px, 5vw, 56px) clamp(56px, 8vw, 96px); }
h1 { font-weight: 900; letter-spacing: -.05em; line-height: .95; font-size: clamp(2.2rem, 6vw, 3.25rem); }
h2 { font-weight: 900; letter-spacing: -.04em; line-height: 1.05; font-size: 1.5rem; }
.lede { margin-top: 16px; max-width: 34em; font-size: 1.125rem; }
.notice { margin-top: 24px; padding: 14px 18px; border-radius: var(--r); background: var(--card); box-shadow: var(--shadow);
  border-left: 3px solid var(--ink-2); }
.gm { font-weight: 900; letter-spacing: -.04em; white-space: nowrap; }
.gm svg { width: .78em; height: .78em; fill: currentColor; vertical-align: -.04em; margin-right: .25em; }
.card { background: var(--card); border-radius: var(--r-lg); box-shadow: var(--shadow); }

/* rides: subheading, stat cards, rides grouped by month as cards, skipped activities folded away at the end */
.summary { margin-top: 10px; font-size: .9375rem; }
.stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 28px; }
.stats div { padding: 16px 18px 14px; background: var(--card); border-radius: var(--r-lg); box-shadow: var(--shadow); }
.stats b { display: block; font-weight: 900; font-size: 2rem; letter-spacing: -.04em; line-height: 1; }
.stats span { display: block; margin-top: 6px; font-size: .8125rem; color: var(--ink-2); line-height: 1.3; }
.scale { margin-top: 10px; font-size: .8125rem; }
/* filters: each group a labelled track (label, then options); a result line with a reset under them */
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 28px; }
.seg, .dates { display: flex; align-items: center; gap: 2px; padding: 3px; background: var(--paper-2); border-radius: var(--r);
  font-size: .875rem; color: var(--ink-2); }
.seg > span, .dates label { padding: 0 8px 0 9px; font-size: .8125rem; font-weight: 500; white-space: nowrap; }
.dates label + input + label { padding-left: 10px; }
.seg button { height: 30px; padding: 0 12px; border-radius: var(--r-sm); border: 0; background: none; cursor: pointer; color: var(--ink-2);
  font: inherit; font-weight: 700; white-space: nowrap; transition: background .15s, color .15s; }
.seg button:hover { color: var(--ink); }
.seg button[aria-pressed="true"] { background: var(--raised); color: var(--ink); box-shadow: var(--shadow); }
.dates input { height: 30px; min-width: 0; padding: 0 8px; border: 0; border-radius: var(--r-sm); background: var(--raised); color: var(--ink);
  font: inherit; font-weight: 500; box-shadow: var(--shadow); }
.seg button:focus-visible, .dates input:focus-visible, .reset:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.shown { display: flex; align-items: baseline; gap: 12px; margin-top: 12px; font-size: .875rem; }
.reset { padding: 0; border: 0; background: none; cursor: pointer; font: inherit; font-weight: 700; color: var(--ink);
  text-decoration: underline; text-underline-offset: 3px; }
.nomatch { margin-top: 24px; }
[hidden] { display: none !important; }
.month { margin: 40px 0 12px; font-size: 1.125rem; letter-spacing: -.02em; color: var(--ink-2); }
.rides { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }
.ride { scroll-margin-top: 140px; padding: 12px 16px 10px; background: var(--card); border-radius: var(--r-lg); box-shadow: var(--shadow);
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 16px; }
.ride:target { box-shadow: 0 0 0 2px var(--accent), var(--shadow); }
.ride .meta { font-size: .8125rem; }
.ride h3 { font-weight: 800; letter-spacing: -.02em; font-size: 1.05rem; line-height: 1.2; margin-top: 0; }
.ride .score { align-self: start; display: flex; align-items: center; gap: 10px; }
.ride .score b { font-weight: 900; font-size: 1.8rem; letter-spacing: -.05em; line-height: .9; }
.badge { display: inline-block; font-family: var(--mono); font-weight: 500; font-size: .6875rem; letter-spacing: .1em;
  text-transform: uppercase; padding: 4px 9px 3px; border-radius: 999px; background: var(--paper-2); color: var(--ink); }
.mix { grid-column: 1 / -1; display: flex; gap: 2px; height: 6px; margin-top: 8px; border-radius: 999px; overflow: hidden; }
.mix i { height: 100%; min-width: 2px; }
.mixtext { grid-column: 1 / -1; margin-top: 4px; font-size: .8125rem; }
.foot { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 16px;
  margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--rule); }
.status { font-size: .9375rem; color: var(--ink-2); }
.status .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--ink-2); margin-right: 8px; vertical-align: 1px; opacity: .6; }
.status .dot.on { background: var(--accent); opacity: 1; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
.strava-link { display: inline-flex; align-items: center; gap: 5px; font-size: .875rem; font-weight: 700; text-underline-offset: 3px; }
.strava-link .ico { width: 14px; height: 14px; }
.skipped-list { margin-top: 40px; padding: 4px 20px; background: var(--card); border-radius: var(--r-lg); box-shadow: var(--shadow); }
.skipped-list summary { cursor: pointer; padding: 12px 0; font-weight: 700; color: var(--ink-2); }
.skipped-list ul { list-style: none; margin: 0 0 8px; padding: 0; }
.skipped-list li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 16px; padding: 10px 0; border-top: 1px solid var(--rule);
  color: var(--ink-2); font-size: .9375rem; }
.empty { margin-top: 24px; padding: 22px 24px; border: 1.5px dashed var(--rule); border-radius: var(--r-lg); display: grid; gap: 14px; justify-items: start; }
@media (max-width: 560px) {
  .stats { gap: 8px; }
  .stats b { font-size: 1.5rem; }
  .stats div { padding: 12px 12px 10px; border-radius: var(--r); }
  /* filters go full width so the tracks line up and nothing overflows */
  .seg, .dates { flex: 1 1 100%; }
  .seg button, .dates input { flex: 1; width: 0; padding: 0 6px; }
  .ride { padding: 12px 14px 10px; }
  .ride .score b { font-size: 1.6rem; }
}

/* settings: each group a card */
.group { margin-top: 20px; padding: 24px; background: var(--card); border-radius: var(--r-lg); box-shadow: var(--shadow);
  display: grid; gap: 18px; max-width: 42em; }
h1 + form > .group:first-of-type, h1 + .notice + form > .group:first-of-type { margin-top: 28px; }
.check { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; align-items: start; cursor: pointer; }
.check input { width: 20px; height: 20px; margin: 3px 0 0; accent-color: var(--ink); }
.check b { font-weight: 700; }
.check span { grid-column: 2; color: var(--ink-2); font-size: .9375rem; }

footer { border-top: 1px solid var(--rule); }
footer .wrap { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 24px; padding-block: 20px 40px; font-size: .875rem; color: var(--ink-2); }
"""

MARK = ('<svg width="24" height="24" viewBox="0 0 64 64" fill="currentColor" aria-hidden="true"><rect x="4" y="6" width="56" '
        'height="12"/><rect x="4" y="26" width="24" height="12"/><rect x="36" y="26" width="24" height="12"/><circle cx="10" '
        'cy="52" r="6"/><circle cx="32" cy="52" r="6"/><circle cx="54" cy="52" r="6"/></svg>')
GM = ('<span class="gm"><svg viewBox="0 0 64 64" aria-hidden="true"><rect x="4" y="6" width="56" height="12"/><rect x="4" y="26" '
      'width="24" height="12"/><rect x="36" y="26" width="24" height="12"/><circle cx="10" cy="52" r="6"/><circle cx="32" '
      'cy="52" r="6"/><circle cx="54" cy="52" r="6"/></svg>gritmeter</span>')

THEME_SWITCH = """<div class="theme" role="group" aria-label="Colour theme">
<button type="button" data-t="" aria-pressed="true" aria-label="Auto theme" title="auto: follow your device"><svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/></svg></button>
<button type="button" data-t="light" aria-pressed="false" aria-label="Light theme" title="light"><svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/></svg></button>
<button type="button" data-t="dark" aria-pressed="false" aria-label="Dark theme" title="dark"><svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg></button>
</div>"""

# 16px line icons in the theme switch's style (1.8 stroke, round caps), drawn in currentColor
ICONS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "retry": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v6h6"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v6h6"/><path d="M12 7v5l3 2"/>',
    "external": '<path d="M7 17 17 7M8 7h9v9"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "logout": '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3"/>',
}


def icon(name):
    return (f'<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


THEME_JS = """<script>
(function () {
  var root = document.documentElement, btns = document.querySelectorAll(".theme button");
  function set(t) {
    if (t) root.dataset.theme = t; else delete root.dataset.theme;
    btns.forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.t === t); });
    try { localStorage.setItem("theme", t); } catch (e) {}
  }
  btns.forEach(function (b) { b.addEventListener("click", function () { set(b.dataset.t); }); });
  var saved = ""; try { saved = localStorage.getItem("theme") || ""; } catch (e) {}
  set(saved);
})();
</script>"""

# flash messages, picked by a fixed code in the redirect URL (never echo free text from the query string)
NOTICES = {
    "connected": "connected. new rides you upload to Strava get a surface summary.",
    "noscope": "connected, but without permission to edit your activities, so gritmeter can rate rides but not add "
               "the summary. sign in again and keep that box ticked.",
    "saved": "settings saved.",
    "backfill": "your recent rides are in the queue. they are rated here only; add the summary ride by ride.",
    "out": "signed out.",
    "gone": "disconnected. gritmeter's access is revoked and everything it stored about you is deleted.",
    "expired": "that sign-in link expired or was cancelled. try again.",
    "failed": "couldn't reach Strava. try again in a minute.",
}


def shell(title, body, athlete=None, csrf="", current=""):
    """Full page: header (nav when signed in), main, credits footer."""
    nav, signout = "", ""
    if athlete:
        link = lambda href, ico, text: (f'<a href="{href}"{" aria-current=page" if current == href else ""}>'
                                        f'{icon(ico)}{text}</a>')
        nav = (f'<nav aria-label="main"><div class="tabs">{link("/", "list", "rides")}'
               f'{link("/settings", "sliders", "settings")}</div></nav>')
        signout = (f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}">'
                   f'<button class="linkish" type="submit" aria-label="sign out">{icon("logout")}<span class="label">sign out</span>'
                   f'</button></form>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{escape(title)} · gritmeter</title>
<link rel="icon" type="image/svg+xml" href="https://gritmeter.app/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Epilogue:wght@100..900&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<header class="top"><div class="wrap">
<a class="logo" href="/" aria-label="gritmeter home">{MARK}gritmeter</a>
{nav}<div class="tools">{signout}{THEME_SWITCH}</div>
</div></header>
<main><div class="wrap">{body}</div></main>
<footer><div class="wrap">
<p>map data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a></p>
<p class="pwrd"><img class="on-light" src="/strava/api_logo_pwrdBy_strava_horiz_black.svg" alt="Powered by Strava" width="138" height="14"><img class="on-dark" src="/strava/api_logo_pwrdBy_strava_horiz_white.svg" alt="Powered by Strava" width="138" height="14"><span>{GM} is not affiliated with or endorsed by Strava.</span></p>
</div></footer>
{THEME_JS}
</body>
</html>"""


def notice(code):
    return f'<p class="notice" role="status">{escape(NOTICES[code])}</p>' if code in NOTICES else ""


def login_page(authorize_url, code=""):
    body = f"""<h1>sign in</h1>
<p class="lede">connect your Strava account and {GM} adds a surface summary to every ride you upload. here you can
see what it rated and change how it writes.</p>
{notice(code)}
<p style="margin-top:32px"><a class="connect" href="{escape(authorize_url)}"><img src="/strava/btn_strava_connect_with_orange.svg" alt="Connect with Strava" width="237" height="48"></a></p>
<p class="muted" style="margin-top:16px;font-size:.8125rem;max-width:46ch">gritmeter asks to read your activities, including private
ones, and to edit them so it can add its lines. it never changes what you wrote.</p>"""
    return shell("sign in", body)


# ---------------------------------------------------------------- activities

def _n(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def _mix(pct):
    """Proportional bar plus the same numbers in text (colour is never the only carrier)."""
    parts = [(k, p) for k, p in pct.items() if p > 0]
    bar = "".join(f'<i style="flex:{p};background:var(--{GRADES[k].replace("?", "u").lower()})" title="{GRADES[k]} '
                  f'{rr.DESC_NAMES.get(k, k)} {p:.0f}%"></i>' for k, p in parts)
    text = " · ".join(f"{int(p + .5)}% {rr.DESC_NAMES.get(k, k)}" for k, p in sorted(parts, key=lambda kp: -kp[1])
                      if p >= 1)
    return f'<div class="mix" aria-hidden="true">{bar}</div><p class="mixtext mono muted">{escape(text)}</p>'


def _action(activity_id, action, ico, label, csrf, line=True):
    return (f'<form method="post" action="/activity/{activity_id}/{action}"><input type="hidden" name="csrf" '
            f'value="{csrf}"><button class="btn sm{" line" if line else ""}" type="submit">{icon(ico)}{label}</button></form>')


def _strava_link(activity_id):
    return (f'<a class="strava-link" href="https://www.strava.com/activities/{activity_id}">View on Strava'
            f'{icon("external")}</a>')


def _skip_reason(detail):
    if (detail or "").startswith("sport_type"):
        kind = detail.split(" ", 1)[1]
        return "virtual ride" if kind == "VirtualRide" else "not a ride"
    return {"indoor or manual": "indoor or manual entry", "no GPS stream": "no GPS"}.get(detail, detail or "")


def _state(job, csrf):
    """(status text, action button) for a ride."""
    s, d, aid = job["status"], job["detail"], job["activity_id"]
    dot = '<span class="dot"></span>'
    if s == "queued":
        what = {"write": "adding the summary", "remove": "removing the summary"}.get(job["action"], "in the queue")
        return f"{dot}{what}…", ""
    if s == "done":
        return ('<span class="dot on"></span>in the description',
                _action(aid, "remove", "minus", "remove", csrf))
    if s == "rated":
        why = {"auto-add off": "auto-add is off", "private ride": "private ride", "past ride": "ride from before you joined"}
        if (d or "").startswith("type off "):
            why[d] = RIDE_TYPE_NAMES.get(d.split()[-1], "this ride type") + " are off"
        return f"{dot}not added: {why.get(d, d)}", _action(aid, "add", "plus", "add to description", csrf, line=False)
    if s == "dryrun":
        return f"{dot}rated in a test run", _action(aid, "add", "plus", "add to description", csrf, line=False)
    if s == "removed":
        return f"{dot}removed from the description", _action(aid, "add", "plus", "add again", csrf)
    if s == "failed":
        return f"{dot}couldn't process this ride", _action(aid, "retry", "retry", "try again", csrf)
    return escape(s), ""


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")


def _day(start_date):
    """'sun 20 oct' from an ISO local start time ('' if unknown)."""
    try:
        d = datetime.date.fromisoformat(start_date[:10])
    except (TypeError, ValueError):
        return ""
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1][:3]}"


def _month(start_date):
    try:
        d = datetime.date.fromisoformat(start_date[:10])
    except (TypeError, ValueError):
        return "date unknown"
    return f"{MONTHS[d.month - 1]} {d.year}"


def _ride(job, csrf):
    aid, r = job["activity_id"], job["result"]
    name = escape(job["name"] or f"activity {aid}")
    meta = " · ".join(x for x in (_day(job["start_date"]), f'{r["km"]} km' if r else "") if x)
    score = (f'<div class="score"><span class="badge">{escape(r["label"])}</span><b class="tnum">{int(r["score"] + .5)}</b></div>'
             if r else '<div class="score"></div>')
    status, button = _state(job, csrf)
    kind = {"road": "road", "mostly road": "road", "mixed": "mixed"}.get(r["label"], "gravel") if r else ""
    added = "added" if job["status"] == "done" else "not-added"
    return (f'<li class="ride" id="ride-{aid}" data-kind="{kind}" data-added="{added}" data-type="{escape(job.get("sport_type") or "")}" data-date="{(job["start_date"] or "")[:10]}"><div><p class="meta mono muted tnum">{meta}</p><h3>{name}</h3></div>{score}'
            f'{_mix(r["pct"]) if r else ""}'
            f'<div class="foot"><p class="status">{status}</p><div class="actions">{button}{_strava_link(aid)}</div></div></li>')


def _skipped(jobs):
    """Activities gritmeter didn't rate, folded into one list at the end."""
    if not jobs:
        return ""
    def row(j):
        meta = " · ".join(x for x in (_day(j["start_date"]), escape(_skip_reason(j["detail"]))) if x)
        return (f'<li id="ride-{j["activity_id"]}"><span>{escape(j["name"] or "activity")}'
                f'<span class="mono tnum"> · {meta}</span></span>{_strava_link(j["activity_id"])}</li>')
    rows = "".join(row(j) for j in jobs)
    return (f'<details class="skipped-list"><summary>{_n(len(jobs), "activity").replace("activitys", "activities")} '
            f'skipped</summary><ul>{rows}</ul></details>')


FILTER_JS = """<script>
(function () {
  var sel = {}, segs = document.querySelectorAll(".seg"), rides = document.querySelectorAll(".ride");
  try { sel = JSON.parse(sessionStorage.getItem("filters") || "{}"); } catch (e) {}
  function apply() {
    segs.forEach(function (g) {
      g.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.v === (sel[g.dataset.f] || "")); });
    });
    var shown = 0;
    rides.forEach(function (li) {
      var d = li.dataset.date;
      var ok = (!sel.kind || li.dataset.kind === sel.kind) && (!sel.added || li.dataset.added === sel.added)
        && (!sel.type || li.dataset.type === sel.type) && (!sel.from || (d && d >= sel.from)) && (!sel.to || (d && d <= sel.to));
      li.hidden = !ok; if (ok) shown++;
    });
    document.querySelectorAll(".dates input").forEach(function (i) { i.value = sel[i.dataset.d] || ""; });
    // keep the range valid: "from" can't pass "to" and the other way round
    document.getElementById("d-from").max = sel.to || "";
    document.getElementById("d-to").min = sel.from || "";
    document.querySelectorAll(".month-group").forEach(function (m) { m.hidden = !m.querySelector(".ride:not([hidden])"); });
    var on = Object.keys(sel).some(function (k) { return sel[k]; });
    document.querySelector(".shown").hidden = !on;
    document.querySelector(".shown span").textContent = shown + " of " + rides.length + " rides";
    document.querySelector(".nomatch").hidden = shown > 0;
    try { sessionStorage.setItem("filters", JSON.stringify(sel)); } catch (e) {}
  }
  document.querySelector(".reset").addEventListener("click", function () { sel = {}; apply(); });
  segs.forEach(function (g) {
    g.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      sel[g.dataset.f] = b.dataset.v; apply();
    });
  });
  document.querySelectorAll(".dates input").forEach(function (i) {
    i.addEventListener("change", function () { sel[i.dataset.d] = i.value; apply(); });
  });
  apply();
})();
</script>"""


def activities_page(athlete, jobs, csrf, code=""):
    rides = [j for j in jobs if j["status"] != "skipped"]
    skipped = [j for j in jobs if j["status"] == "skipped"]
    rated = [j["result"] for j in rides if j["result"]]
    written = sum(j["status"] == "done" for j in rides)
    warn = notice("noscope") if "activity:write" not in (athlete.get("scope") or "") and code != "noscope" else ""
    summary = f'<p class="summary mono muted">{escape(athlete["firstname"])}</p>' if athlete.get("firstname") else ""

    stats = ""
    if rated:
        avg = sum(r["score"] for r in rated) / len(rated)
        stats = (f'<div class="stats"><div><b class="tnum">{len(rated)}</b><span>rides rated</span></div>'
                 f'<div><b class="tnum">{written}</b><span>with the summary</span></div>'
                 f'<div><b class="tnum">{avg:.1f}</b><span>average roughness, {rr.label(avg)}</span></div></div>'
                 '<p class="scale muted">roughness runs from 0, smooth tarmac, to 100, very rough.</p>')

    groups, listing = {}, ""
    for j in rides:  # already newest first
        groups.setdefault(_month(j["start_date"]), []).append(j)
    for month, js in groups.items():
        listing += (f'<section class="month-group"><h2 class="month">{month}</h2><ul class="rides">'
                    + "".join(_ride(j, csrf) for j in js) + "</ul></section>")

    filters = ""
    types = sorted({j["sport_type"] for j in rides if j.get("sport_type")})
    if len(rated) > 1:
        def seg(name, label, opts):
            btns = "".join(f'<button type="button" data-v="{v}" aria-pressed="{str(v == "").lower()}">{t}</button>' for v, t in opts)
            return (f'<div class="seg" role="group" aria-labelledby="f-{name}" data-f="{name}">'
                    f'<span id="f-{name}">{label}</span>{btns}</div>')
        filters = ('<div class="filters">'
                   + seg("kind", "surface", (("", "all"), ("road", "road"), ("mixed", "mixed"), ("gravel", "gravel")))
                   + seg("added", "summary", (("", "all"), ("added", "added"), ("not-added", "not added")))
                   + (seg("type", "type", (("", "all"),) + tuple((t, RIDE_TYPE_NAMES.get(t, t).replace(" rides", "").replace("road and other", "road/other")) for t in types))
                      if len(types) > 1 else "")
                   + '<div class="dates"><label for="d-from">from</label><input type="date" id="d-from" data-d="from">'
                     '<label for="d-to">to</label><input type="date" id="d-to" data-d="to"></div>'
                   + '</div><p class="shown muted" hidden><span aria-live="polite"></span>'
                     '<button type="button" class="reset">show all</button></p>'
                   + '<p class="nomatch muted" hidden>no rides match these filters.</p>')

    backfill = (f'<div class="empty">{"<p>nothing yet. new rides show up here a minute or two after you upload them.</p>" if not jobs else ""}'
                f'<p class="muted">want to see what {GM} makes of rides from before you joined? it rates rides from '
                f'the last 14 days here, without touching their descriptions.</p>'
                f'<form method="post" action="/backfill"><input type="hidden" name="csrf" value="{csrf}">'
                f'<button class="btn line sm" type="submit">{icon("history")}rate my last 14 days</button></form></div>')
    # while anything is queued, reload every few seconds (keeps the scroll position) so its status updates
    refresh = ('<script>setTimeout(function () { location.reload(); }, 4000);</script>'
               if any(j["status"] == "queued" for j in jobs) else "")
    body = (f"{refresh}<h1>your rides</h1>{summary}{notice(code)}{warn}{stats}{filters}"
            f"{listing}{_skipped(skipped)}{backfill}")
    return shell("rides", body + (FILTER_JS if filters else ""), athlete, csrf, current="/")


# ---------------------------------------------------------------- settings

def settings_page(athlete, csrf, code=""):
    checked = lambda key: " checked" if athlete.get(key) else ""
    on = store.sport_types(athlete)
    types = "".join(f'<label class="check"><input type="checkbox" name="type_{t}" value="1"'
                    f'{" checked" if on is None or t in on else ""}><b>{name}</b></label>'
                    for t, name in RIDE_TYPE_NAMES.items())
    scope_ok = "activity:write" in (athlete.get("scope") or "")
    permission = ("" if scope_ok else
                  '<div class="group"><h2>permission</h2><p>gritmeter can\'t edit your activities, so it can\'t add '
                  'the summary. sign in again and keep "upload activities" ticked.</p>'
                  f'<p><a class="btn" href="/login">{icon("key")}sign in again</a></p></div>')
    body = f"""<h1>settings</h1>{notice(code)}
<form method="post" action="/settings">
<input type="hidden" name="csrf" value="{csrf}">
<div class="group">
<h2>writing</h2>
<label class="check"><input type="checkbox" name="auto_write" value="1"{checked("auto_write")}>
<b>add the summary to new rides automatically</b>
<span>when this is off, {GM} still rates every ride here, and you add the summary ride by ride.</span></label>
<label class="check"><input type="checkbox" name="skip_private" value="1"{checked("skip_private")}>
<b>leave rides that only you can see alone</b>
<span>they are still rated here.</span></label>
</div>
<div class="group">
<h2>which rides</h2>
<p class="muted" style="font-size:.9375rem">{GM} adds the summary to these ride types. the others are still rated
here, and you can add it ride by ride.</p>
{types}
</div>
<p style="margin-top:20px"><button class="btn" type="submit">{icon("check")}save</button></p>
</form>
{permission}
<div class="group">
<h2>disconnect</h2>
<p>this revokes {GM}'s access on Strava and deletes everything it stored about you: your sign-in, settings and ride
results. summaries already in your descriptions stay; remove them from the rides page first if you want them gone.</p>
<form method="post" action="/disconnect"><input type="hidden" name="csrf" value="{csrf}">
<button class="btn line" type="submit">{icon("trash")}disconnect and delete my data</button></form>
</div>"""
    return shell("settings", body, athlete, csrf, current="/settings")
