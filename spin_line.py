#!/usr/bin/env python3
"""
Spin Line — Table Tennis Match Winner & Total Games
--------------------------------------------------------------
Match-level predictor for the bet365 table-tennis cup/league slate --
Setka Cup, TT Cup, Czech Liga Pro, TT Elite Series -- built on BetsAPI
(https://betsapi.com, docs at https://betsapi.com/docs/). Markets:
Match Winner (log5 per-game rate -> race-to-N match probability) and
Total Games (recency-weighted pace, Poisson-priced, same pattern as
every other tool in this suite). NOTE: originally scoped as "Total
Points" per the user's request -- re-scoped to Total Games after a
live sample confirmed BetsAPI's event/history has no per-set POINTS
data at all, only the final games-won score. See the CONFIRMED block
below.

WHY BETSAPI, NOT THESTATSAPI: TheStatsAPI (used by Match IQ / Cards &
Corners IQ / Player Stat Model) is football-only -- confirmed, not
assumed, per the user. BetsAPI is the only realistic source actually
covering these specific niche cup leagues (cross-checked against
tt-lossbeater.com, a real public TT analytics product that names
BetsAPI as its own source for this exact same set of leagues, and
states plainly that no feed sells ready-made player form for table
tennis -- every rate has to be reconstructed from finished matches,
exactly what this file does).

THIS IS A FIRST DRAFT BUILT AGAINST DOCUMENTATION, NOT A LIVE KEY.
BetsAPI is a paid service (~$10/mo, no free tier) -- nothing in this
sandbox can hit a real endpoint to confirm response shapes. Every
assumption below is flagged. Run debug_tt_leagues.py once you have a
BETSAPI_TOKEN (ideally via the "Debug TT Leagues" GitHub Action, not
locally, so the token never has to leave GitHub Secrets) BEFORE
trusting this file's output -- it dumps real league IDs and one real
event/history response so the parsing below can be corrected against
actual field names instead of guesses.

CONFIRMED (2026-10-02, via debug_tt_leagues.py --confirm against a
live BETSAPI_TOKEN): all 4 league IDs below are real, from BetsAPI's
own /v3/league list (sport_id=92) -- Setka Cup=22307, Czech Liga
Pro=22742 (cc=cz), TT Cup=29097 (cc=cz -- NOT TT Cup Women id=30462 or
TT Cup Ukraine id=22534, which are separate competitions BetsAPI lists
under similar names), TT Elite Series=29128. Also confirmed in that
same run: the dual-host fallback (api.b365api.com / api.betsapi.com)
and retrying 502/503 as well as plain timeouts are BOTH necessary --
GitHub Actions' connections to BetsAPI are genuinely flaky (several
pages of that debug run needed a retry or the other host to get a
response at all), not a one-off. _get() below carries the same
retry/fallback logic debug_tt_leagues.py ended up needing.

CONFIRMED (2026-10-02, via debug_tt_leagues.py --dump-sample against
league_id=29128, a real "ss"-bearing event/history + event/view
response -- see spin_line_sample_29128_20261002.json):
  1. /v3/events/upcoming response shape CONFIRMED correct as originally
     assumed: {"success":1,"results":[{"id":..., "time":"<unix epoch
     string>", "time_status":"0", "league":{"id":...,"name":...,"cc":...},
     "home":{"id":...,"name":...}, "away":{...}, "ss":null}, ...]}.
  2. /v1/event/history response shape CONFIRMED: {"success":1,
     "results":{"h2h":[...], "home":[...past events...],
     "away":[...past events...]}} -- "results" is a DICT at the top
     level (not a list), already handled correctly below. Each past
     event carries "ss" -- CONFIRMED to always be formatted
     "home_team_games-away_team_games" FOR THAT SPECIFIC PAST MATCH,
     regardless of which player's history array it's returned under
     (verified by cross-referencing the same past event id appearing
     identically in both players' "home"/"away" arrays). This means
     the side a given number belongs to must be worked out per-event
     by comparing that event's own home/away team id against the
     player being projected -- NOT by always reading the first number
     as "this player's games", which was the original (wrong) design
     and the confirmed cause of implausible win-streak clustering seen
     in a live TT Elite Series run. project_player() below now does
     this comparison. CONFIRMED ABSENT: there is no "scores" dict (or
     any other per-set POINTS field) anywhere in event/history or
     event/view -- only the final games-won "ss" score exists. The
     original "Total Points" market can't be built from this API at
     all; re-scoped to "Total Games" (total games played per match,
     using the real "ss" data) -- the user's explicit choice when
     presented with this finding.
  3. Match format: race-to-3-games (best of 5) CONFIRMED by
     event/view's "extra":{"bestofsets":"5"} field in the live sample,
     matching the MATCH_GAMES_TO_WIN=3 assumption already in place.
  4. /v1/event/view response shape: "results" is a LIST here (unlike
     event/history's dict) -- {"success":1,"results":[{"id":...,
     "time":..., "time_status":"0"|"3", "home":{...}, "away":{...},
     "ss":null|"<score>", "extra":{"bestofsets":"5","stadium_data":{...}},
     "confirmed_at":...}]}. The live sample event was pre-match
     (ss=null), so a FINISHED event/view's exact "ss" shape is
     inferred (not directly sampled) to match event/history's
     confirmed "home_games-away_games" convention -- lower risk now
     that the convention is confirmed elsewhere, but worth a second
     look if the results tracker's own parsing ever looks off.

CONFIRMED (2026-10-02, via debug_tt_leagues.py --dump-finished-sample
against an already-FINISHED Setka Cup match -- see
spin_line_finished_sample_22307_20261002.json): a finished match's own
/v1/event/view DOES carry per-game data after all -- "scores":
{"1":{"home":"10","away":"12"}, "2":{...}, "3":{...}} (final score of
each individual game) and a full point-by-point "timeline" (each point
tagged with game number "gm", scoring side "te" as "0"=home/"1"=away,
and the running score "ss"). This does NOT contradict the "no points
data" finding above -- that finding is about event/history's past-event
entries specifically (confirmed to carry only the final "ss", nothing
per-game), which is what feeds player projections. event/view's richer
data only exists per SPECIFIC already-known event_id, one call each --
there's no bulk/history endpoint that returns it for a whole player's
past matches. This is what 1st Game Winner is built on: for each event
in a player's event/history list, get_game1_result() makes one extra
event/view call (cached locally forever by event_id in
docs/spin-line/event_view_cache.json, since a finished match's result
never changes) to learn who won game 1 of that specific past match.
MAX_NEW_CACHE_FETCHES_PER_RUN caps new lookups per run so the cache
builds up coverage gradually across many daily runs instead of spiking
API usage on day one -- early runs will have thin 1st Game Winner
coverage until the cache matures. User's explicit choice (via
AskUserQuestion) over skipping the market or fetching uncached/smaller
history every run.

Usage:
    pip3 install requests --break-system-packages
    export BETSAPI_TOKEN=your_token_here
    python3 spin_line.py               # today's fixtures across all leagues
    python3 spin_line.py 2026-10-05    # a specific date
    python3 spin_line.py --auto        # non-interactive, for GitHub Actions

Output:
    docs/spin-line/index.html
    docs/spin-line/spin_line.json
"""

import os
import sys
import math
import json
import time
import requests
from datetime import date, datetime

# BetsAPI's own docs name a second load-balancer domain specifically
# "in case you have issues with api.b365api.com" -- and a live debug
# run (2026-10-02) confirmed GitHub Actions genuinely needs it: several
# requests only succeeded after falling back to the other host, or
# after a retry past a transient 502. Both hosts are tried every cycle
# before backing off -- see _get() below.
HOSTS = ["https://api.b365api.com", "https://api.betsapi.com"]
SPORT_ID = 92  # confirmed via betsapi.com/docs/GLOSSARY.html
TOKEN = os.environ.get("BETSAPI_TOKEN")

# CONFIRMED 2026-10-02 via debug_tt_leagues.py --confirm against a live
# token (see module docstring) -- all 4 are real /v3/league ids.
LEAGUE_TARGETS = [
    {"id": 22307, "name": "Setka Cup"},
    {"id": 22742, "name": "Czech Liga Pro"},
    {"id": 29097, "name": "TT Cup"},
    {"id": 29128, "name": "TT Elite Series"},
]

RECENT_WEIGHT = 0.65
DEFAULT_LINE_FACTOR = 0.90  # total games per match is a small, tight range
                             # (3-5, since it's race-to-3-of-5) -- same high
                             # factor as before works fine here too, it's
                             # the absolute line (safe_line's round_to=0.5)
                             # that keeps it sane for a small integer stat
MATCH_GAMES_TO_WIN = 3  # race-to-3 (best of 5) -- CONFIRMED via event/view's
                         # "extra":{"bestofsets":"5"} in the live sample

# --- 1st Game Winner event/view cache ------------------------------------
# A finished match's result never changes, so once we know who won game 1
# of a specific past event, that's cached forever by event_id -- no TTL,
# no re-fetching. See module docstring's CONFIRMED block (2026-10-02,
# --dump-finished-sample) for why this needs its own API call per past
# event instead of coming for free out of event/history.
EVENT_VIEW_CACHE_PATH = "docs/spin-line/event_view_cache.json"
MAX_NEW_CACHE_FETCHES_PER_RUN = 300  # bounds API usage per run -- the cache
                                      # builds up real coverage gradually
                                      # across many daily runs rather than
                                      # trying to backfill everything (and
                                      # blow the rate limit) in one go
GAME1_MIN_SAMPLES = 3  # don't post a 1st Game Winner leg off 1-2 cached
                        # past matches -- too thin to trust

_event_view_cache = {}
_new_cache_fetches = 0


def load_event_view_cache():
    global _event_view_cache
    if os.path.exists(EVENT_VIEW_CACHE_PATH):
        try:
            with open(EVENT_VIEW_CACHE_PATH) as f:
                _event_view_cache = json.load(f)
        except Exception as e:
            print(f"  [!] couldn't read event_view_cache.json ({e}) -- starting empty")
            _event_view_cache = {}
    return _event_view_cache


def save_event_view_cache():
    os.makedirs(os.path.dirname(EVENT_VIEW_CACHE_PATH), exist_ok=True)
    with open(EVENT_VIEW_CACHE_PATH, "w") as f:
        json.dump(_event_view_cache, f)


def get_game1_result(event_id):
    """Returns 'home' or 'away' -- who won game 1 of this specific,
    already-FINISHED past match -- using the local cache first and only
    calling event/view on a cache miss (and only while under this run's
    MAX_NEW_CACHE_FETCHES_PER_RUN budget). Returns None if not cached,
    the budget's used up, the match isn't actually finished yet, or
    the response can't be parsed -- callers should just skip that
    historical match rather than guess."""
    global _new_cache_fetches
    key = str(event_id)
    if key in _event_view_cache:
        return _event_view_cache[key]
    if _new_cache_fetches >= MAX_NEW_CACHE_FETCHES_PER_RUN:
        return None

    _new_cache_fetches += 1
    try:
        data = _get("v1", "/event/view", {"event_id": event_id})
    except Exception:
        return None
    results = data.get("results", []) if isinstance(data, dict) else []
    event = results[0] if isinstance(results, list) and results else (results if isinstance(results, dict) else {})
    if str(event.get("time_status")) != "3":
        return None  # not actually finished -- don't cache, may resolve later

    scores = event.get("scores")
    if not isinstance(scores, dict) or "1" not in scores:
        return None
    g1 = scores.get("1") or {}
    try:
        h, a = int(g1.get("home")), int(g1.get("away"))
    except (TypeError, ValueError):
        return None

    winner = "home" if h > a else "away"
    _event_view_cache[key] = winner  # finished result never changes -- cache forever
    return winner


def _get(version, path, params=None, cycles=4, timeout=20):
    """version is 'v1' or 'v3'. Same dual-host + retry-past-5xx logic
    debug_tt_leagues.py needed against this same API -- see the module
    docstring's CONFIRMED note. A real 4xx (bad token, bad params) is
    NOT retried, since waiting won't fix that."""
    if not TOKEN:
        print("Set the BETSAPI_TOKEN environment variable first.")
        raise SystemExit(1)
    p = dict(params or {})
    p["token"] = TOKEN
    last_err = None
    for cycle in range(1, cycles + 1):
        for host in HOSTS:
            try:
                r = requests.get(f"{host}/{version}{path}", params=p, timeout=timeout)
                if r.status_code >= 500:
                    last_err = requests.exceptions.HTTPError(f"{r.status_code} from {host}")
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                last_err = e
        if cycle < cycles:
            time.sleep(min(5 * cycle, 20))
    raise last_err


def poisson_pmf(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def prob_over(lam, line):
    threshold = math.floor(line) + 1
    cum = sum(poisson_pmf(i, lam) for i in range(threshold))
    return 1 - cum


def hit_rate(values, line):
    if not values:
        return None
    hits = sum(1 for v in values if v > line)
    return {"hits": hits, "total": len(values)}


def safe_line(lam, factor=DEFAULT_LINE_FACTOR, round_to=0.5):
    if lam is None:
        return None
    raw = lam * factor
    line = math.floor(raw / round_to) * round_to
    return max(line, round_to)


def cap_outliers(values, multiplier=1.5, min_cap=3):
    """Same outlier-clipping convention as Euro Ice/Strike Zone -- clip
    before the recency-weighted average, leave the raw list alone for
    display/hit_rate. See euro_ice.py's cap_outliers() for the full
    reasoning; identical here."""
    if len(values) < 3:
        return values
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    cap = max(median * multiplier, min_cap)
    return [min(v, cap) for v in values]


def recency_weighted_avg(values, weight=RECENT_WEIGHT):
    """values: oldest-first. Blends a recency-weighted average of the
    capped values with nothing else (no season-average leg to blend
    against here -- BetsAPI has no season-aggregate endpoint the way
    Highlightly/TheStatsAPI do, only raw match history), so this is
    simpler than project_team_goals()'s two-source blend. weight is
    kept as a named constant for consistency with the rest of the
    suite even though it isn't blending two sources here."""
    capped = cap_outliers(values)
    if not capped:
        return None
    if len(capped) == 1:
        return capped[0]
    n = len(capped)
    wts = [1.4 ** i for i in range(n)]
    return sum(w * v for w, v in zip(wts, capped)) / sum(wts)


# --- Match format math -------------------------------------------------

def log5(rate_a, rate_b):
    """Standard Bradley-Terry / log5 combination: given two independent
    per-game win RATES, returns A's per-game win probability against B.
    Clamped away from 0/1 so a small sample's 100% or 0% rate can't
    produce a divide-by-zero or a false-certainty 100%/0% output."""
    a = min(max(rate_a, 0.03), 0.97)
    b = min(max(rate_b, 0.03), 0.97)
    num = a * (1 - b)
    den = num + b * (1 - a)
    return num / den if den else 0.5


def race_to_n_prob(p, n=MATCH_GAMES_TO_WIN):
    """Probability of winning a best-of-(2n-1) match (first to n games),
    given an independent per-game win probability p. Standard
    combinatorial race-win formula:
        P = sum_{k=0}^{n-1} C(n-1+k, k) * p^n * (1-p)^k
    (A wins exactly the (n+k)-th game, having already taken n-1 of the
    first n-1+k.) Same shape as a tennis/volleyball set-race calculator."""
    total = 0.0
    for k in range(n):
        total += math.comb(n - 1 + k, k) * (p ** n) * ((1 - p) ** k)
    return total


# --- BetsAPI calls -------------------------------------------------------

def get_upcoming_matches(league_id, target_date):
    """v3/events/upcoming -- see UNVERIFIED ASSUMPTION 2 for the
    response shape this expects. time_status "0" = not started is
    BetsAPI's general (not table-tennis-specific) convention."""
    data = _get("v3", "/events/upcoming", {
        "sport_id": SPORT_ID, "league_id": league_id,
        "day": target_date.strftime("%Y%m%d"),
    })
    results = data.get("results", []) if isinstance(data, dict) else []
    return [m for m in results if str(m.get("time_status")) == "0"]


def get_event_history(event_id, qty=10):
    """v1/event/history -- see UNVERIFIED ASSUMPTION 3. Returns
    (home_history, away_history), each a list of past-event dicts,
    oldest-first (sorted here since arrival order isn't documented)."""
    try:
        data = _get("v1", "/event/history", {"event_id": event_id, "qty": qty})
    except Exception as e:
        print(f"    [!] event/history failed for event {event_id}: {e}")
        return [], []
    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        return [], []
    block = results[0] if isinstance(results, list) else results
    home = block.get("home", []) or []
    away = block.get("away", []) or []

    def _sort(evs):
        return sorted(evs, key=lambda e: e.get("time", "") or "")

    return _sort(home), _sort(away)


def parse_games_won(ss):
    """'3-1' -> (3, 1), HOME - AWAY for that specific match (CONFIRMED
    2026-10-02 against a real event/history sample -- see module
    docstring). This is NOT "subject's games first" -- which side is
    "home" or "away" varies per past event and has nothing to do with
    which player's history array the event is returned under. Callers
    must compare the event's own home/away team id against the player
    being projected to know which number is theirs -- see
    project_player() below. Returns None on anything unparseable."""
    if not ss:
        return None
    parts = str(ss).replace(" ", "").split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def project_player(history, player_id, weight=RECENT_WEIGHT):
    """From a player's own past-event list (oldest-first), derive:
      - match_games_avg: recency-weighted avg of TOTAL games played per
        match (games won + games lost) -- the Total Games market's pace
        indicator. (Originally scoped as a points-based "Total Points"
        pace -- re-scoped after a live sample confirmed BetsAPI's
        event/history has no per-set points data at all, only the
        final games-won score. See module docstring CONFIRMED block.)
      - game_win_rate: recency-weighted fraction of individual
        games/sets won across those same matches.
      - game1_win_rate: recency-weighted fraction of MATCHES where this
        player won game 1 specifically -- the 1st Game Winner market's
        pace indicator. Needs one extra event/view lookup per past
        match (via get_game1_result(), cached by event_id -- see module
        docstring's CONFIRMED block on why event/history alone can't
        tell us this), so coverage is partial until the cache matures;
        None until at least GAME1_MIN_SAMPLES past matches are cached.
      - results: W/L per match (most recent last), for the Win Streak
        panel and for display.
    player_id is this player's own BetsAPI team id (as returned on the
    UPCOMING match's home/away block) -- REQUIRED, because "ss" is
    always "home_games-away_games" for that specific past match, not
    "this player's games first" (CONFIRMED against a live sample --
    see module docstring and parse_games_won()). Each event's own
    home/away ids are compared against player_id to attribute the two
    numbers correctly; a fixed-position read here was the confirmed
    cause of corrupted win rates and implausible streaks seen in a live
    TT Elite Series run (any match where the player was the away side
    got silently flipped). Returns None if there's no usable history."""
    totals, game_rates, results, game1_results = [], [], [], []
    for ev in history:
        parsed = parse_games_won(ev.get("ss"))
        if parsed is None:
            continue
        home_games, away_games = parsed
        home_id = (ev.get("home") or {}).get("id")
        away_id = (ev.get("away") or {}).get("id")
        if str(home_id) == str(player_id):
            won, lost, player_side = home_games, away_games, "home"
        elif str(away_id) == str(player_id):
            won, lost, player_side = away_games, home_games, "away"
        else:
            continue  # event doesn't actually list this player on either side
        played = won + lost
        if not played:
            continue
        totals.append(played)
        game_rates.append(won / played)
        results.append("W" if won > lost else "L")

        event_id = ev.get("id")
        if event_id is not None:
            g1_winner = get_game1_result(event_id)
            if g1_winner is not None:
                game1_results.append(1.0 if g1_winner == player_side else 0.0)

    if not totals:
        return None

    return {
        "match_games_avg": round(recency_weighted_avg(totals), 2),
        "match_games_history": totals,
        "game_win_rate": round(recency_weighted_avg(game_rates), 3) if game_rates else None,
        "game1_win_rate": round(recency_weighted_avg(game1_results), 3) if len(game1_results) >= GAME1_MIN_SAMPLES else None,
        "game1_n": len(game1_results),
        "results": results,  # oldest -> newest
        "n_games": len(totals),
    }


# --- Win streak (consecutive MATCH wins, same walk-backward concept as
# Euro Ice's Real Streak, applied to win/loss instead of a count
# threshold) ---------------------------------------------------------

REAL_STREAK_MIN_LENGTH = 3


def _current_win_streak(results):
    streak = 0
    for r in reversed(results):
        if r == "W":
            streak += 1
        else:
            break
    return streak


def render_match_card(league_name, home_name, away_name, p_home_game, p_away_game,
                       p_home_match, p_away_match, total_lambda, total_line, total_prob,
                       home_proj, away_proj):
    home_hist = "/".join(home_proj["results"][-5:]) or "-"
    away_hist = "/".join(away_proj["results"][-5:]) or "-"

    win_bar = f"""<div style="margin:10px 0 6px 0">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;font-size:12px;margin-bottom:4px">
        <div>
          <div>{away_name} {p_away_match*100:.0f}%</div>
          <div style="font-size:10px;color:var(--sub);margin-top:2px">last 5 (old→new): {away_hist}</div>
        </div>
        <div style="text-align:right">
          <div>{home_name} {p_home_match*100:.0f}%</div>
          <div style="font-size:10px;color:var(--sub);margin-top:2px">last 5 (old→new): {home_hist}</div>
        </div>
      </div>
      <div style="display:flex;height:10px;border-radius:999px;overflow:hidden;background:var(--panel2)">
        <div style="width:{p_away_match*100:.1f}%;background:#ff4d5a"></div>
        <div style="width:{p_home_match*100:.1f}%;background:#4ea1ff"></div>
      </div>
    </div>"""

    return f"""<div class="builderPanel">
      <div style="font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:.03em">{league_name}</div>
      <h3 style="margin:2px 0 4px 0;font-size:17px">{away_name} vs {home_name} — Total {total_lambda:.1f} games</h3>
      <p style="margin:0;color:var(--sub);font-size:13px">Per-game win rate: {away_name} {p_away_game*100:.0f}% · {home_name} {p_home_game*100:.0f}% | O{total_line} games {total_prob*100:.0f}%</p>
      {win_bar}
    </div>"""


def build_legs_and_cards(target_date):
    """Returns (legs, cards_html, streak_entries). legs feeds the Safest
    Bet Builder; cards_html is the per-match card; streak_entries feeds
    the Win Streak panel. Mirrors euro_ice.py's build_legs_and_cards()
    shape so the rest of the suite's conventions (results tracker
    wiring, HTML template, builder JS) all carry over unchanged."""
    legs = []
    cards = ""
    streak_entries = []

    for league in LEAGUE_TARGETS:
        if league["id"] is None:
            print(f"Skipping {league['name']} -- league id not yet confirmed "
                  f"(run debug_tt_leagues.py --confirm with a real BETSAPI_TOKEN)")
            continue

        print(f"Scanning {league['name']}...")
        try:
            matches = get_upcoming_matches(league["id"], target_date)
        except Exception as e:
            print(f"  [!] couldn't fetch fixtures for {league['name']}: {e}")
            continue
        print(f"  {len(matches)} fixtures found")

        for m in matches:
            home, away = m.get("home", {}), m.get("away", {})
            home_name, away_name = home.get("name", "Home"), away.get("name", "Away")
            event_id = m.get("id")
            match_label = f"{away_name} vs {home_name}"
            match_date = m.get("time", "")

            home_hist, away_hist = get_event_history(event_id)
            home_proj = project_player(home_hist, home.get("id"))
            away_proj = project_player(away_hist, away.get("id"))

            if not home_proj or not away_proj:
                continue

            # --- Match Winner ------------------------------------------------
            if home_proj["game_win_rate"] is not None and away_proj["game_win_rate"] is not None:
                p_home_game = log5(home_proj["game_win_rate"], away_proj["game_win_rate"])
                p_away_game = 1 - p_home_game
                p_home_match = race_to_n_prob(p_home_game)
                p_away_match = race_to_n_prob(p_away_game)

                for name, p_match, proj, opp in (
                    (home_name, p_home_match, home_proj, away_name),
                    (away_name, p_away_match, away_proj, home_name),
                ):
                    legs.append({
                        "match": match_label, "subject": name,
                        "market": f"{name} to win",
                        "prob": round(p_match * 100),
                        "hit_rate": None,
                        "category": f"{league['name']} Match Winner",
                        "detail": f"per-game win rate {proj['game_win_rate']*100:.0f}% vs {opp}",
                        "history": "/".join(proj["results"][-5:]) or None,
                        "event_id": event_id, "league_name": league["name"],
                        "home_name": home_name, "away_name": away_name, "match_date": match_date,
                    })
            else:
                p_home_game = p_away_game = p_home_match = p_away_match = None

            # --- 1st Game Winner ----------------------------------------------
            # Needs game1_win_rate on both sides (only populated once
            # GAME1_MIN_SAMPLES past matches have a cached event/view result
            # -- see project_player() and the module docstring's CONFIRMED
            # block). Coverage is partial until the cache matures, so this
            # leg simply won't appear for most matchups early on.
            if home_proj["game1_win_rate"] is not None and away_proj["game1_win_rate"] is not None:
                p_home_g1 = log5(home_proj["game1_win_rate"], away_proj["game1_win_rate"])
                p_away_g1 = 1 - p_home_g1

                for name, p_g1, proj, opp in (
                    (home_name, p_home_g1, home_proj, away_name),
                    (away_name, p_away_g1, away_proj, home_name),
                ):
                    legs.append({
                        "match": match_label, "subject": name,
                        "market": f"{name} to win Game 1",
                        "prob": round(p_g1 * 100),
                        "hit_rate": None,
                        "category": f"{league['name']} 1st Game Winner",
                        "detail": f"wins game 1 in {proj['game1_win_rate']*100:.0f}% of their last {proj['game1_n']} cached matches (their own matches, not just vs {opp})",
                        "history": None,
                        "event_id": event_id, "league_name": league["name"],
                        "home_name": home_name, "away_name": away_name, "match_date": match_date,
                    })

            # --- Total Games (per-player pace + blended match total) --------
            for name, proj, opp_name in ((home_name, home_proj, away_name), (away_name, away_proj, home_name)):
                line = safe_line(proj["match_games_avg"])
                if not line:
                    continue
                prob = prob_over(proj["match_games_avg"], line)
                legs.append({
                    "match": match_label, "subject": name,
                    "market": f"{name}'s matches Over {line} Total Games",
                    "prob": round(prob * 100),
                    "hit_rate": hit_rate(proj["match_games_history"], line),
                    "category": f"{league['name']} Player Game Total",
                    "detail": f"avg {proj['match_games_avg']} games/match (their own matches, not just vs {opp_name})",
                    "history": "/".join(str(v) for v in proj["match_games_history"]) or None,
                    "event_id": event_id, "league_name": league["name"],
                    "home_name": home_name, "away_name": away_name, "match_date": match_date,
                })

            total_lambda = (home_proj["match_games_avg"] + away_proj["match_games_avg"]) / 2
            line = safe_line(total_lambda)
            if line:
                prob = prob_over(total_lambda, line)
                legs.append({
                    "match": match_label, "subject": match_label,
                    "market": f"Match Over {line} Total Games",
                    "prob": round(prob * 100),
                    "hit_rate": None,  # blended pace, not a real shared history -- same
                                       # reasoning as Euro Ice's Game Total leg
                    "category": f"{league['name']} Game Total",
                    "detail": f"blended pace {round(total_lambda, 1)} games",
                    "history": None,
                    "event_id": event_id, "league_name": league["name"],
                    "home_name": home_name, "away_name": away_name, "match_date": match_date,
                })

                if p_home_match is not None:
                    cards += render_match_card(
                        league["name"], home_name, away_name, p_home_game, p_away_game,
                        p_home_match, p_away_match, total_lambda, line, prob, home_proj, away_proj,
                    )

            for name, proj, is_home in ((home_name, home_proj, True), (away_name, away_proj, False)):
                streak_len = _current_win_streak(proj["results"])
                if streak_len >= REAL_STREAK_MIN_LENGTH:
                    streak_entries.append({
                        "player": name, "opponent": away_name if is_home else home_name,
                        "is_home": is_home, "league": league["name"], "date": match_date,
                        "streak_len": streak_len, "full_sample": streak_len >= len(proj["results"]),
                        "home_name": home_name, "away_name": away_name,
                    })

    streak_entries.sort(key=lambda e: -e["streak_len"])
    return legs, cards, streak_entries


STREAK_ENTRY_TEMPLATE = """<div style="background:var(--panel2);border-radius:8px;padding:10px 12px;margin:8px 0;display:flex;gap:10px;align-items:flex-start">
  <div style="min-width:56px;text-align:center;background:var(--bg);border:1px solid #f59e0b;border-radius:8px;padding:6px 4px;flex-shrink:0">
    <div style="font-size:9px;color:var(--sub)">STREAK</div>
    <div style="font-size:17px;font-weight:bold;color:#f59e0b">{streak_len}{plus}</div>
  </div>
  <div style="flex:1;min-width:0">
    <div style="font-size:10px;color:var(--sub)">{league}</div>
    <div style="font-size:14px;font-weight:bold;margin:1px 0 4px">{player} vs {opponent}</div>
    <div style="font-size:10px;color:var(--sub)">{streak_len} straight match wins</div>
  </div>
</div>"""

STREAK_PANEL_TEMPLATE = """<div class="builderPanel">
  <div class="builderTitle">🔥 Win Streak</div>
  <div style="font-size:11px;color:var(--sub);margin-bottom:10px">
    Genuinely CONSECUTIVE match wins (no break), reconstructed from recent finished matches --
    informational, not blended into the Match Winner or Total Games projections above.
  </div>
  {entries}
</div>"""


def render_streak_panel(streak_entries):
    if not streak_entries:
        return ""
    html = "".join(
        STREAK_ENTRY_TEMPLATE.format(
            streak_len=e["streak_len"], plus="+" if e["full_sample"] else "",
            league=e["league"], player=e["player"], opponent=e["opponent"],
        )
        for e in streak_entries
    )
    return STREAK_PANEL_TEMPLATE.format(entries=html)


HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spin Line — {date}</title>
<style>
  :root{{--bg:#0b0f14; --panel:#121820; --panel2:#161d27; --border:#233040; --text:#e8edf2; --sub:#8b98a8; --amber:#facc15; --green:#22c55e;}}
  body{{margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:16px; max-width:640px; margin:0 auto;}}
  h1{{font-size:20px; margin-bottom:4px;}}
  .sub{{color:var(--sub); font-size:13px; margin-bottom:18px;}}
  .builderPanel{{background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:14px;}}
  .builderTitle{{font-size:15px; font-weight:800; margin-bottom:10px;}}
  .builderToggles{{display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px; font-size:12px;}}
  .builderToggles label{{display:flex; align-items:center; gap:4px; color:var(--text); cursor:pointer;}}
  .builderControls{{display:flex; gap:8px; align-items:center; margin-bottom:6px; flex-wrap:wrap;}}
  .builderControls label{{font-size:12px; color:var(--sub);}}
  .builderControls input{{width:70px; background:var(--panel2); border:1px solid var(--border); color:var(--text); border-radius:6px; padding:6px 8px; font-size:13px;}}
  .builderBtn{{background:var(--green); color:#04140a; font-weight:700; border:none; padding:7px 14px; border-radius:6px; font-size:13px; cursor:pointer;}}
  .builderBtnAlt{{background:var(--panel2); border:1px solid var(--border); color:var(--text); padding:7px 14px; border-radius:6px; font-size:13px; cursor:pointer;}}
  .builderResult{{font-size:12px; color:var(--sub);}}
  .legRow{{display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid var(--border);}}
  .footnote{{font-size:11px; color:var(--sub); text-align:center; margin-top:20px; line-height:1.6;}}
</style></head>
<body>
  <h1>🏓 Spin Line — Match Winner &amp; Total Games</h1>
  <div class="sub">Setka Cup · TT Cup · Czech Liga Pro · TT Elite Series — {date} · generated {generated}</div>
  <p style="text-align:center;margin:4px 0 0;font-size:12px"><a href="results/index.html" style="color:#f59e0b;text-decoration:none">📊 Results Tracker</a></p>

  <div id="builderPanel" class="builderPanel">
    <div class="builderTitle">🎯 Safest Bet Builder</div>
    <div id="builderCategoryToggles" class="builderToggles"></div>
    <div class="builderControls">
      <label>Target odds:</label>
      <input type="number" step="0.1" min="1.1" value="5.0" id="targetOdds">
      <label>Max legs:</label>
      <input type="number" step="1" min="2" value="8" id="maxLegs">
      <button class="builderBtn" onclick="buildSafest()">Build</button>
      <button class="builderBtnAlt" onclick="buildSafest()">🔀 Shuffle</button>
    </div>
    <div id="builderResult" class="builderResult">
      Untick any market you don't want considered, set a target odds and leg cap, then tap
      Build. Caps at 2 legs per player/matchup to avoid stacking a player's own legs against
      the same game's blended total. Tap Shuffle for a fresh pick without changing your settings.
    </div>
  </div>

  {streak_panel}

  {cards}

  <div class="footnote">
    Match Winner uses each player's recency-weighted per-GAME win rate, combined via log5 into
    a per-game probability, then priced as a race-to-{games_to_win} match using the standard
    combinatorial formula. Total Games blends each player's own recency-weighted total-games-
    per-match pace (games won + games lost, not a real shared history) and prices with a
    Poisson distribution, same convention as every other tool in this suite -- re-scoped from
    an originally planned Total Points market after a live sample confirmed BetsAPI has no
    per-set points data for these leagues, only the final games-won score. 1st Game Winner uses
    each player's own recency-weighted rate of winning game 1 of their matches, combined via
    log5 — coverage builds up gradually over time via a local cache (one extra lookup per past
    match, capped per run), so it won't appear for every matchup yet. Win Streak is
    informational only — genuinely consecutive match wins, not blended into any projection.
  </div>

<script>
const LEGS = {legs_json};

function shuffleArr(arr) {{
  for (let i = arr.length - 1; i > 0; i--) {{
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }}
  return arr;
}}
function tieredShuffle(legs, bandSize) {{
  const bands = {{}};
  legs.forEach(l => {{
    const band = Math.floor(l.prob / bandSize);
    (bands[band] = bands[band] || []).push(l);
  }});
  const keys = Object.keys(bands).map(Number).sort((a,b) => b-a);
  let result = [];
  keys.forEach(k => {{ result = result.concat(shuffleArr(bands[k])); }});
  return result;
}}
function initToggles() {{
  const container = document.getElementById('builderCategoryToggles');
  const cats = [...new Set(LEGS.map(l => l.category))];
  container.innerHTML = cats.map(c => `
    <label><input type="checkbox" class="catToggle" value="${{c}}" checked> ${{c}}</label>
  `).join('');
}}
function buildSafest() {{
  const target = parseFloat(document.getElementById('targetOdds').value) || 5.0;
  const maxLegs = parseInt(document.getElementById('maxLegs').value) || 8;
  const activeCats = [...document.querySelectorAll('.catToggle:checked')].map(el => el.value);

  const byCategory = {{}};
  LEGS.filter(l => l.prob > 0 && activeCats.includes(l.category)).forEach(l => {{
    (byCategory[l.category] = byCategory[l.category] || []).push(l);
  }});
  const categories = Object.keys(byCategory);
  categories.forEach(c => {{ byCategory[c] = tieredShuffle(byCategory[c], 5); }});
  const cursor = {{}};
  categories.forEach(c => cursor[c] = 0);

  const chosen = [];
  const subjectCount = {{}};
  let combinedOdds = 1;
  let addedThisPass = true;

  while (addedThisPass && combinedOdds < target && chosen.length < maxLegs) {{
    addedThisPass = false;
    for (const cat of categories) {{
      if (combinedOdds >= target || chosen.length >= maxLegs) break;
      const arr = byCategory[cat];
      while (cursor[cat] < arr.length) {{
        const leg = arr[cursor[cat]];
        cursor[cat]++;
        const count = subjectCount[leg.subject] || 0;
        if (count >= 2) continue;
        chosen.push(leg);
        combinedOdds *= 100 / leg.prob;
        subjectCount[leg.subject] = count + 1;
        addedThisPass = true;
        break;
      }}
    }}
  }}

  const out = document.getElementById('builderResult');
  if (!chosen.length) {{ out.innerHTML = 'No legs available to build from.'; return; }}

  const rows = chosen.map(l => `
    <div class="legRow">
      <span>${{l.match}}<br><span style="color:var(--amber)">${{l.market}}</span> <span style="color:var(--sub)">· ${{l.category}}</span>
      ${{l.detail ? `<br><span style="color:var(--sub);font-size:10px">${{l.detail}}</span>` : ''}}
      ${{l.history ? `<br><span style="color:var(--sub);font-size:10px">recent: ${{l.history}}</span>` : ''}}</span>
      <span style="text-align:right"><span style="color:var(--amber);font-weight:bold">${{l.prob}}%</span>${{l.hit_rate ? `<br><span style="color:var(--sub);font-size:11px">${{l.hit_rate.hits}}/${{l.hit_rate.total}}</span>` : ''}}</span>
    </div>
  `).join('');

  const capNote = chosen.length >= maxLegs && combinedOdds < target
    ? ' (hit the leg cap before reaching target — raise Max legs or lower Target odds)'
    : (combinedOdds < target ? ' (ran out of legs before reaching target)' : '');

  out.innerHTML = `
    <div style="color:var(--text);font-size:13px;margin-bottom:6px">
      ${{chosen.length}} legs · est. combined odds ~<b>${{combinedOdds.toFixed(2)}}</b>${{capNote}}
    </div>
    ${{rows}}
    <div style="color:var(--sub);font-size:10px;margin-top:8px;line-height:1.4">
      Estimate multiplies each leg's fair odds (100/probability) — real sportsbook odds
      include their margin, so treat this as a ranking tool, not a firm price.
    </div>
  `;
}}
initToggles();
</script>
</body></html>
"""


def render_html(legs, cards, streak_entries, target_date):
    return HTML_TEMPLATE.format(
        date=target_date.isoformat(),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        games_to_win=MATCH_GAMES_TO_WIN,
        streak_panel=render_streak_panel(streak_entries),
        cards=cards or "<p style='color:var(--sub);text-align:center'>No matchups had enough data for a full card today.</p>",
        legs_json=json.dumps(legs),
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--auto":
        target = date.fromisoformat(sys.argv[1])
    else:
        target = date.today()

    print(f"Fetching Spin Line slate for {target.isoformat()}…")
    load_event_view_cache()
    print(f"  1st Game Winner cache: {len(_event_view_cache)} finished matches known so far")
    legs, cards, streak_entries = build_legs_and_cards(target)
    print(f"  1st Game Winner cache: {_new_cache_fetches} new lookup(s) this run, "
          f"{len(_event_view_cache)} total cached")
    save_event_view_cache()

    if not legs:
        print("No usable legs today -- either no confirmed league ids yet (see "
              "LEAGUE_TARGETS), no fixtures found, or no player had enough history "
              "to project. Nothing to publish; docs/ left as whatever the last "
              "successful run published.")
        raise SystemExit(0)

    print(f"  Win Streak: {len(streak_entries)} player-entries")

    html = render_html(legs, cards, streak_entries, target)
    os.makedirs("docs/spin-line", exist_ok=True)
    with open("docs/spin-line/index.html", "w") as f:
        f.write(html)
    with open("docs/spin-line/spin_line.json", "w") as f:
        json.dump(legs, f, indent=2, default=str)

    try:
        import spin_line_results_tracker as results_tracker
        results_tracker.run_results_tracker(legs)
    except Exception as e:
        print(f"\n[!] Results tracker failed, but the rest of this run succeeded: {e}")

    print(f"\nDone. {len(legs)} legs written to docs/spin-line/index.html")
