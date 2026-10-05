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

CONFIRMED (2026-10-02, via a live event/history + event/view sample --
see spin_line.py's module docstring): "ss" is always HOME-AWAY games
won for that specific match, in BOTH event/history and event/view --
the assumption this file made for event/view's own "ss" field was
right, and it's also the convention spin_line.py's project_player()
now uses for event/history's past-event entries (an earlier version of
that function assumed "ss" was subject-first, which was wrong and is
now fixed). The original "Player Pace (Total Points)" scanner was
re-scoped to "Player Games (Total Games)" once a live sample confirmed
there's no per-set "scores" field on event/history's past-event
entries (only the final games-won "ss") -- and that market was then
REMOVED ENTIRELY on 2026-10-04 (along with the blended "Game Total"
leg) once the user confirmed bet365 doesn't actually offer either as a
real market for these leagues. No "player_games"/"game_total" scanner
exists in this file any more; see spin_line.py's own module docstring
for the matching removal on the prediction side.

CONFIRMED (2026-10-05, from a live dashboard showing 22,524 pending vs
only 490 ever verified, and every newer market stuck at 0/0): two
compounding bugs. (1) log_todays_signals() used to log a pick for
EVERY match in the day's full slate on every hourly run (spin_line.py
moved to hourly rebuilds on 2026-10-03 to fix stale card pairings) --
but these studio leagues keep reshuffling who's actually paired
against whom, so most logged picks described a pairing that got
reassigned before it was ever played: that event_id never reaches
time_status=3, so the pick sits "pending" forever. Fixed by only
logging a pick once its match's kickoff is within LOG_WINDOW_HOURS --
by then the pairing has very likely settled. (2) date_key was computed
as `(date or "")[:10]` assuming an ISO datetime string, but
spin_line.py's leg["match_date"] is actually a raw BetsAPI UNIX epoch
string (e.g. "1790944800") -- slicing that gives back the same epoch
string, which SORTS BEFORE any real ISO date lexicographically, so
verify_pending_results' `entry["date_key"] >= today` gate never
skipped anything: every pending entry, even ones for matches that
hadn't kicked off yet, was treated as "due to check" immediately. With
thousands of phantom entries from bug (1) sitting earliest in log
order, the fixed 60-per-run verification budget got burned on them
every single run, and genuinely verifiable newer-market entries never
got reached. Fixed with _date_key()/_kickoff_epoch(), which parse the
real epoch correctly. The pre-existing backlog these two bugs produced
is pruned outright by prune_dead_pending() rather than kept around.

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
    """'3-1' -> (3, 1) as (HOME, AWAY) games won. CONFIRMED 2026-10-02
    against a live sample -- "ss" is always home-away for that specific
    match, in both event/history and event/view (see module docstring
    and spin_line.py's parse_games_won(), which now uses this same
    convention)."""
    if not ss:
        return None
    parts = str(ss).replace(" ", "").split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def parse_game1_winner(event):
    """'home' or 'away' -- who won game 1 of this FINISHED match. Unlike
    event/history's past-event entries (only the final "ss"), a
    finished match's own event/view DOES carry a "scores" dict with the
    final score of each individual game -- CONFIRMED 2026-10-02 via
    debug_tt_leagues.py --dump-finished-sample (see spin_line.py's
    module docstring and get_game1_result(), which this mirrors for
    verification instead of prediction). Returns None if "scores" or
    game 1 specifically isn't present/parseable."""
    scores = event.get("scores")
    if not isinstance(scores, dict) or "1" not in scores:
        return None
    g1 = scores.get("1") or {}
    try:
        h, a = int(g1.get("home")), int(g1.get("away"))
    except (TypeError, ValueError):
        return None
    return "home" if h > a else "away"


def parse_game1_score(event):
    """(home_points, away_points) for game 1 of this FINISHED match, or
    None if unparseable -- same "scores" dict as parse_game1_winner,
    just returning the exact points instead of collapsing them to a
    winner (mirrors spin_line.py's get_game1_score(), used for
    verifying 1st Game Correct Score picks)."""
    scores = event.get("scores")
    if not isinstance(scores, dict) or "1" not in scores:
        return None
    g1 = scores.get("1") or {}
    try:
        return int(g1.get("home")), int(g1.get("away"))
    except (TypeError, ValueError):
        return None


def _entry_id(scanner, subject, event_id, market):
    raw = f"{scanner}|{subject}|{event_id}|{market}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


LOG_WINDOW_HOURS = 2  # only log a pick once its match's kickoff is this close -- see
                       # module docstring's CONFIRMED block (2026-10-05): logging every
                       # match in the day's full slate on every hourly run flooded this
                       # log with picks for pairings that got reshuffled before they were
                       # ever actually played. By this point the pairing has very likely
                       # settled (user's explicit choice over a 1-hour window).

PENDING_EXPIRY_HOURS = 48  # a pending pick whose match kicked off this long ago and still
                           # hasn't resolved is almost certainly a phantom pairing that got
                           # reshuffled before it was ever played -- prune it outright
                           # (user's explicit choice over tagging it "expired" and keeping
                           # it) rather than let it sit in the log forever eating into
                           # verify_pending_results' per-run budget.


def _kickoff_epoch(date_str):
    """Match kickoff as a UTC epoch int. leg["match_date"] (and so
    entry["match_date"]) is normally a raw BetsAPI UNIX epoch STRING
    (e.g. "1790944800"), but tests use an ISO datetime string (e.g.
    "2026-10-05T12:00:00+00:00") -- handles either. Returns None if
    unparseable."""
    if not date_str:
        return None
    s = str(date_str)
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return None


def _date_key(date_str):
    """Normalizes match_date into a plain YYYY-MM-DD, handling the same
    raw-epoch-vs-ISO-string situation as _kickoff_epoch -- see this
    file's module docstring (CONFIRMED 2026-10-05) for why this matters:
    the old `(date or "")[:10]` slice, applied to a real epoch string,
    produced a result that always sorted BEFORE any genuine ISO date,
    silently breaking verify_pending_results' "wait until the match is
    actually in the past" gate."""
    epoch = _kickoff_epoch(date_str)
    if epoch is None:
        return (date_str or "")[:10]
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()


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


def log_todays_signals(legs, log, now=None):
    """Logs Match Winner, 1st Game Winner, Game Handicap, Correct Score,
    and 1st Game Correct Score legs. (Player Game Total / Game Total
    were REMOVED 2026-10-04 -- user-confirmed bet365 doesn't offer
    either as a market for these leagues, so there was nothing to
    verify against a real bet in the first place.)

    Only logs a leg once its match's kickoff is within LOG_WINDOW_HOURS
    -- see this file's module docstring (CONFIRMED 2026-10-05) for why:
    logging every match in the day's full slate on every hourly run
    flooded the log with picks for pairings that got reshuffled before
    they were ever actually played. `now` is injectable (as a UTC epoch
    float) for deterministic tests; defaults to the real current time."""
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    existing_ids = {e["id"] for e in log}
    added = 0
    not_yet_close = 0

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
            "match_date": date, "date_key": _date_key(date),
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending", "result": None, "actual": None,
        })
        existing_ids.add(eid)
        added += 1

    for leg in legs:
        kickoff = _kickoff_epoch(leg.get("match_date"))
        if kickoff is None or not (0 <= kickoff - now <= LOG_WINDOW_HOURS * 3600):
            not_yet_close += 1
            continue
        cat = leg.get("category", "")
        if cat.endswith("Match Winner"):
            add("match_winner", leg["subject"], leg["market"], leg.get("detail"),
                leg["league_name"], leg["event_id"], leg["match_date"],
                leg["home_name"], leg["away_name"])
        elif cat.endswith("1st Game Winner"):
            add("game1_winner", leg["subject"], leg["market"], leg.get("detail"),
                leg["league_name"], leg["event_id"], leg["match_date"],
                leg["home_name"], leg["away_name"])
        elif cat.endswith("Game Handicap"):
            # -1.5/+1.5 is the only line this market ever uses (see
            # spin_line.py's module docstring/race_scoreline_probs) --
            # no line to re-parse out of the market string.
            add("game_handicap", leg["subject"], leg["market"], leg.get("detail"),
                leg["league_name"], leg["event_id"], leg["match_date"],
                leg["home_name"], leg["away_name"])
        elif cat.endswith("1st Game Correct Score"):
            # Checked BEFORE the plain "Correct Score" branch below --
            # "...1st Game Correct Score" also ends with "Correct Score",
            # so the more specific category must be matched first or it
            # would always fall into the match-level branch instead.
            # predicted scoreline is embedded the same way as the match-
            # level Correct Score, just "Game 1" in the middle of the
            # market string instead of straight after the name.
            try:
                ws, ls = leg["market"].split("to win Game 1 ")[1].split("-")
                int(ws), int(ls)
            except (IndexError, ValueError):
                continue
            add("game1_correct_score", leg["subject"], leg["market"], f"score={ws}-{ls}",
                leg["league_name"], leg["event_id"], leg["match_date"],
                leg["home_name"], leg["away_name"])
        elif cat.endswith("Correct Score"):
            # predicted scoreline is embedded in the market string
            # ("{name} to win {gf}-{ga}"), from the SUBJECT's own
            # perspective (gf is always the subject's games) -- pull it
            # back out of the market string directly.
            try:
                gf, ga = leg["market"].split("to win ")[1].split("-")
                int(gf), int(ga)
            except (IndexError, ValueError):
                continue
            add("correct_score", leg["subject"], leg["market"], f"score={gf}-{ga}",
                leg["league_name"], leg["event_id"], leg["match_date"],
                leg["home_name"], leg["away_name"])

    print(f"  Results log: {added} new pick(s) logged, {len(log)} total in log "
          f"({not_yet_close} leg(s) skipped -- not yet within {LOG_WINDOW_HOURS}h of kickoff)")
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


def _verify_game1_winner_entry(entry):
    event = _get_finished_event(entry["event_id"])
    if not event:
        return None
    winner_side = parse_game1_winner(event)
    if winner_side is None:
        return None
    subject_is_home = entry["subject"] == entry["home_name"]
    subject_side = "home" if subject_is_home else "away"
    return {"actual": winner_side, "result": "hit" if winner_side == subject_side else "miss"}


def _verify_game_handicap_entry(entry):
    """-1.5 games handicap: the subject covers if they win by 2+ games
    (3-0 or 3-1), regardless of whether they won the match outright --
    reuses the same "ss" field and home/away logic as Match Winner, no
    new API call shape to trust."""
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
    covered = (subject_games - opp_games) >= 2
    return {"actual": f"{subject_games}-{opp_games}", "result": "hit" if covered else "miss"}


def _verify_correct_score_entry(entry):
    """Exact scoreline, from the subject's own perspective -- reuses the
    same "ss" field as Match Winner/Game Handicap, just compared against
    the predicted (gf, ga) instead of a win/cover threshold."""
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
    predicted = entry["detail"].split("=")[1]  # "gf-ga"
    pred_gf, pred_ga = (int(x) for x in predicted.split("-"))
    hit = (subject_games == pred_gf) and (opp_games == pred_ga)
    return {"actual": f"{subject_games}-{opp_games}", "result": "hit" if hit else "miss"}


def _verify_game1_correct_score_entry(entry):
    """Exact game-1 scoreline, from the subject's own perspective (the
    predicted "ws-ls" is always subject's-points-first, since that's how
    spin_line.py's leg market string is built) -- verified against the
    real per-game "scores" dict, same source as parse_game1_winner."""
    event = _get_finished_event(entry["event_id"])
    if not event:
        return None
    game1 = parse_game1_score(event)
    if game1 is None:
        return None
    home_pts, away_pts = game1
    subject_is_home = entry["subject"] == entry["home_name"]
    subject_pts = home_pts if subject_is_home else away_pts
    opp_pts = away_pts if subject_is_home else home_pts
    predicted = entry["detail"].split("=")[1]  # "ws-ls"
    pred_ws, pred_ls = (int(x) for x in predicted.split("-"))
    hit = (subject_pts == pred_ws) and (opp_pts == pred_ls)
    return {"actual": f"{subject_pts}-{opp_pts}", "result": "hit" if hit else "miss"}


def prune_dead_pending(log, now=None):
    """Removes pending entries whose kickoff was more than
    PENDING_EXPIRY_HOURS ago and still never resolved -- see that
    constant's own comment and this file's module docstring (CONFIRMED
    2026-10-05). `now` is injectable (UTC epoch float) for tests.

    Also repairs date_key on every surviving pending entry via
    _date_key(): entries logged before the 2026-10-05 fix carry the
    broken "raw epoch sliced to 10 chars" date_key (see module
    docstring), which made verify_pending_results treat them as always
    checkable. There's no reason to leave that bug live on existing
    entries just because they predate the fix."""
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    cutoff = now - PENDING_EXPIRY_HOURS * 3600
    kept = []
    pruned = 0
    for entry in log:
        if entry["status"] == "pending":
            kickoff = _kickoff_epoch(entry.get("match_date"))
            if kickoff is not None and kickoff < cutoff:
                pruned += 1
                continue
            entry["date_key"] = _date_key(entry.get("match_date"))
        kept.append(entry)
    if pruned:
        print(f"  Pruned {pruned} dead pending entry(ies) (kickoff was "
              f"{PENDING_EXPIRY_HOURS}h+ ago, never resolved)")
    return kept


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
            elif entry["scanner"] == "game1_winner":
                result = _verify_game1_winner_entry(entry)
            elif entry["scanner"] == "game_handicap":
                result = _verify_game_handicap_entry(entry)
            elif entry["scanner"] == "correct_score":
                result = _verify_correct_score_entry(entry)
            elif entry["scanner"] == "game1_correct_score":
                result = _verify_game1_correct_score_entry(entry)
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

    SCANNER_LABELS = {
        "match_winner": "Match Winner",
        "game1_winner": "1st Game Winner",
        "game_handicap": "Game Handicap (-1.5)",
        "correct_score": "Correct Score",
        "game1_correct_score": "1st Game Correct Score",
    }

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
  Game Handicap and Correct Score both verify against the real final games-won score ("ss");
  1st Game Winner and 1st Game Correct Score both verify against the finished match's "scores"
  dict (final score of each individual game) — all confirmed against live BetsAPI samples, see
  spin_line_results_tracker.py's module docstring. (The old total-games-based markets were
  removed entirely on 2026-10-04 — bet365 doesn't offer them as real markets for these leagues.)
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
    log = prune_dead_pending(log)
    log = log_todays_signals(legs, log)
    log = verify_pending_results(log)
    save_log(log)
    build_results_dashboard(log)
