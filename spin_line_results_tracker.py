#!/usr/bin/env python3
"""
Spin Line Results Tracker
--------------------------------------------------------------
Same architecture as the other trackers in this suite (persistent JSON
log, verify pending entries once the match is finished, render a
dashboard), with one simplification Euro Ice's tracker doesn't have:
every Spin Line leg already carries the BetsAPI event_id it came from
(the v3/events/upcoming and v3/events/ended families share one id
namespace -- see spin_line.py's module docstring), so a pending pick
is verified with a single direct /v1/event/view?event_id=... call
instead of a date+league+name search.

UNVERIFIED ASSUMPTION (separate from, and in addition to, the ones in
spin_line.py): event/view's "ss" field is assumed to be in HOME-AWAY
order (the general BetsAPI convention for a finished event viewed on
its own), which is NOT the same order as event/history's past-event
entries (those are from a specific player's own perspective -- see
parse_games_won() in spin_line.py). If verified results come out
looking backwards (a player who clearly lost shows as "hit" on a
"to win" leg), this is the first place to check.

Designed to be imported and called from spin_line.py's main() --
save this file as spin_line_results_tracker.py in the same folder.

Output:
    docs/spin-line/results/log.json    -- the full log
    docs/spin-line/results/index.html  -- dashboard
"""

import os
import time
import json
import hashlib
import requests
from datetime import datetime, timezone

# See spin_line.py's module docstring (CONFIRMED 2026-10-02) -- a live
# debug run showed GitHub Actions genuinely needs both the fallback
# host and retries past transient 502s against this API, not just
# plain timeouts.
HOSTS = ["https://api.b365api.com", "https://api.betsapi.com"]
TOKEN = os.environ.get("BETSAPI_TOKEN")
LOG_PATH = "docs/spin-line/results/log.json"
DASHBOARD_PATH = "docs/spin-line/results/index.html"


def _get(path, params=None, cycles=3, timeout=20):
    """Results verification failing is non-fatal by design (callers
    treat None as "try again next run"), so this stays quiet on
    failure rather than raising -- but still gets a real chance via
    the same dual-host + 5xx-retry logic spin_line.py uses, instead of
    giving up after one attempt against one host."""
    if not TOKEN:
        return None
    p = dict(params or {})
    p["token"] = TOKEN
    for cycle in range(1, cycles + 1):
        for host in HOSTS:
            try:
                r = requests.get(f"{host}/v1{path}", params=p, timeout=timeout)
            except Exception as e:
                print(f"    [!] verification request failed on {host}: {path} ({e})")
                continue
            if r.status_code >= 500:
                print(f"    [!] {r.status_code} (transient) on {host}: {path}")
                continue
            if r.status_code != 200:
                print(f"    [!] {r.status_code} on {host}: {path}: {r.text[:150]}")
                return None  # a real 4xx won't be fixed by retrying
            return r.json()
        if cycle < cycles:
            time.sleep(min(5 * cycle, 20))
    print(f"    [!] giving up on {path} after {cycles} cycles across both hosts")
    return None


def parse_ss_home_away(ss):
    """'3-1' -> (3, 1) as (HOME, AWAY) games won. See module docstring --
    this order assumption is DIFFERENT from spin_line.py's
    parse_games_won(), which is subject-perspective, not home/away."""
    if not ss:
        return None
    parts = str(ss).replace(" ", "").split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def parse_total_points(event):
    scores = event.get("scores")
    if not isinstance(scores, dict) or not scores:
        return None
    total = 0
    counted = 0
    for set_scores in scores.values():
        if not isinstance(set_scores, dict):
            continue
        h, a = set_scores.get("home"), set_scores.get("away")
        try:
            total += int(h) + int(a)
            counted += 1
        except (TypeError, ValueError):
            continue
    return total if counted else None


def _entry_id(scanner, subject, event_id, market):
    raw = f"{scanner}|{subject}|{event_id}|{market}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_log():
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH) as f:
            return json.load(f)
    except Exception as e:
        print(f"  [!] Couldn't read existing results log ({e}) -- starting fresh.")
        return []


def save_log(entries):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def log_todays_signals(legs, log):
    """Logs Match Winner and Player Pace legs. Game Total legs are
    skipped, same convention/reasoning as Euro Ice's tracker: they
    blend two players' SEPARATE pace histories, not a real shared
    record, so there's no single real "side" being claimed the way
    there is for a Match Winner or a specific player's own pace."""
    existing_ids = {e["id"] for e in log}
    added = 0

    def add(scanner, subject, market, detail, league, event_id, date,
             home_name, away_name):
        nonlocal added
        eid = _entry_id(scanner, subject, event_id, market)
        if eid in existing_ids:
            return
        log.append({
            "id": eid, "scanner": scanner, "subject": subject, "market": market,
            "detail": detail, "league": league, "event_id": event_id,
            "home_name": home_name, "away_name": away_name,
            "match_date": date, "date_key": (date or "")[:10],
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending", "result": None, "actual": None,
        })
        existing_ids.add(eid)
        added += 1

    for leg in legs:
        cat = leg.get("category", "")
        if cat.endswith("Match Winner"):
            add("match_winner", leg["subject"], leg["market"], leg.get("detail"),
                leg["league_name"], leg["event_id"], leg["match_date"],
                leg["home_name"], leg["away_name"])
        elif cat.endswith("Player Pace"):
            # line is embedded in the market string ("... Over {line} ...");
            # pull it back out rather than threading a separate field through
            # just for this.
            try:
                line = float(leg["market"].split("Over ")[1].split(" ")[0])
            except (IndexError, ValueError):
                continue
            add("player_pace", leg["subject"], leg["market"], f"line={line}",
                leg["league_name"], leg["event_id"], leg["match_date"],
                leg["home_name"], leg["away_name"])

    print(f"  Results log: {added} new pick(s) logged, {len(log)} total in log")
    return log


def _get_finished_event(event_id):
    data = _get("/event/view", {"event_id": event_id})
    if not data:
        return None
    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        return None
    event = results[0] if isinstance(results, list) else results
    if str(event.get("time_status")) != "3":  # 3 = ended, general BetsAPI convention
        return None
    return event


def _verify_match_winner_entry(entry):
    event = _get_finished_event(entry["event_id"])
    if not event:
        return None
    games = parse_ss_home_away(event.get("ss"))
    if not games:
        return None
    home_games, away_games = games
    subject_is_home = entry["subject"] == entry["home_name"]
    subject_games = home_games if subject_is_home else away_games
    opp_games = away_games if subject_is_home else home_games
    return {"actual": f"{subject_games}-{opp_games}", "result": "hit" if subject_games > opp_games else "miss"}


def _verify_player_pace_entry(entry):
    event = _get_finished_event(entry["event_id"])
    if not event:
        return None
    total = parse_total_points(event)
    if total is None:
        return None
    line = float(entry["detail"].split("=")[1])
    return {"actual": total, "result": "hit" if total > line else "miss"}


def verify_pending_results(log, max_checks=60):
    today = datetime.now(timezone.utc).date().isoformat()
    checked = 0
    updated = 0

    for entry in log:
        if entry["status"] != "pending":
            continue
        if entry["date_key"] >= today:
            continue
        if checked >= max_checks:
            break
        checked += 1

        result = None
        try:
            if entry["scanner"] == "match_winner":
                result = _verify_match_winner_entry(entry)
            elif entry["scanner"] == "player_pace":
                result = _verify_player_pace_entry(entry)
        except Exception as e:
            print(f"    [!] verification error for entry {entry['id']} ({entry['scanner']}): {e}")
            result = None

        if result:
            entry["status"] = "verified"
            entry["result"] = result["result"]
            entry["actual"] = result["actual"]
            entry["verified_at"] = datetime.now(timezone.utc).isoformat()
            updated += 1

    print(f"  Results verification: checked {checked} pending entries, {updated} newly verified")
    return log


def build_results_dashboard(log):
    verified = [e for e in log if e["status"] == "verified"]
    pending = [e for e in log if e["status"] == "pending"]

    by_scanner = {}
    for e in verified:
        d = by_scanner.setdefault(e["scanner"], {"hit": 0, "miss": 0})
        d[e["result"]] += 1

    SCANNER_LABELS = {"match_winner": "Match Winner", "player_pace": "Player Pace (Total Points)"}

    total_hit = sum(d["hit"] for d in by_scanner.values())
    total_miss = sum(d["miss"] for d in by_scanner.values())
    total = total_hit + total_miss
    overall_pct = round(100 * total_hit / total) if total else None

    rows = ""
    for scanner, label in SCANNER_LABELS.items():
        d = by_scanner.get(scanner, {"hit": 0, "miss": 0})
        n = d["hit"] + d["miss"]
        pct = round(100 * d["hit"] / n) if n else None
        pct_str = f"{pct}%" if pct is not None else "—"
        rows += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)">
  <span>{label}</span>
  <span style="color:var(--green);font-weight:bold">{pct_str}</span>
  <span style="color:var(--sub);font-size:12px">{d['hit']}/{n}</span>
</div>"""

    recent = sorted(verified, key=lambda e: e.get("verified_at", ""), reverse=True)[:30]
    recent_rows = ""
    for e in recent:
        color = "#22c55e" if e["result"] == "hit" else "#ef4444"
        recent_rows += f"""<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
  <span>{e['subject']} — {e['market']}</span>
  <span style="color:{color};font-weight:bold">{e['result'].upper()}</span>
</div>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Results — Spin Line</title>
<style>:root{{--bg:#0b0f14;--panel:#121820;--border:#233040;--text:#e8edf2;--sub:#8b98a8;--green:#22c55e;}}</style></head>
<body style="background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:16px;max-width:640px;margin:0 auto">
<p style="text-align:center;margin-bottom:6px"><a href="../index.html" style="color:#7ec8ff;text-decoration:none;font-size:12px">← Spin Line</a></p>
<h2 style="text-align:center;margin-bottom:2px">📊 Results Tracker</h2>
<p style="text-align:center;color:var(--sub);font-size:11px;margin-top:0">{datetime.now().strftime("%d %b %H:%M")} · every pick, auto-verified against real results</p>

<div style="background:var(--panel);border-radius:12px;padding:16px;margin:14px 0;border:1px solid var(--border);text-align:center">
  <div style="font-size:11px;color:var(--sub)">OVERALL</div>
  <div style="font-size:32px;font-weight:bold;color:var(--green)">{overall_pct if overall_pct is not None else "—"}{"%" if overall_pct is not None else ""}</div>
  <div style="font-size:12px;color:var(--sub)">{total_hit}/{total} verified picks · {len(pending)} pending (match not finished yet)</div>
</div>

<div style="background:var(--panel);border-radius:12px;padding:16px;margin:14px 0;border:1px solid var(--border)">
  <div style="font-weight:bold;margin-bottom:8px">By Category</div>
  {rows}
</div>

<div style="background:var(--panel);border-radius:12px;padding:16px;margin:14px 0;border:1px solid var(--border)">
  <div style="font-weight:bold;margin-bottom:8px">Recent Results</div>
  {recent_rows or '<p style="color:var(--sub);font-size:12px">Nothing verified yet — check back after a few days of picks have had time to play out.</p>'}
</div>

<div style="font-size:11px;color:var(--sub);text-align:center;margin-top:20px;line-height:1.6">
  Game Total legs aren't tracked here — they blend two players' separate pace histories into
  one number, so there's no single real "side" to check against (same reasoning as Euro Ice's
  Game Total). FIRST DRAFT: this tracker's "ss"/"scores" parsing is unverified against a real
  BetsAPI response (see spin_line_results_tracker.py's module docstring) — treat early results
  with real caution until a live run confirms the field shapes are right.
</div>
</body></html>"""

    os.makedirs(os.path.dirname(DASHBOARD_PATH), exist_ok=True)
    with open(DASHBOARD_PATH, "w") as f:
        f.write(html)
    print(f"  Results dashboard: {total} verified, {overall_pct}% overall" if total else "  Results dashboard: no verified picks yet")


def run_results_tracker(legs):
    """Single entry point called from spin_line.py's main()."""
    print("\nRunning results tracker...")
    log = load_log()
    log = log_todays_signals(legs, log)
    log = verify_pending_results(log)
    save_log(log)
    build_results_dashboard(log)
