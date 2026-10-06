#!/usr/bin/env python3
"""
Spin Line — Table Tennis Match Winner, Game Handicap & Correct Score
--------------------------------------------------------------
Match-level predictor for the bet365 table-tennis cup/league slate --
Setka Cup, TT Cup, Czech Liga Pro, TT Elite Series -- built on BetsAPI
(https://betsapi.com, docs at https://betsapi.com/docs/). Markets:
Match Winner (log5 per-game rate -> race-to-N match probability), Game
Handicap (-1.5/+1.5), Correct Score, 1st Game Winner, and 1st Game
Correct Score. NOTE: originally scoped to also include a "Total
Points"/"Total Games" market -- built, then REMOVED on 2026-10-04 once
the user confirmed bet365 doesn't actually offer either as a market for
these leagues (points data was never there either way -- see the
CONFIRMED block below). Every market left in this file is one the user
can actually place on bet365.

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
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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
GAME1_SCORE_MIN_SAMPLES = 15  # minimum POOLED (not per-matchup) game-1
                               # exact-score samples before exposing 1st
                               # Game Correct Score at all -- a game-to-11
                               # (win-by-2) scoreline distribution is a
                               # shared physical pattern across players, so
                               # pooling reaches a usable sample size much
                               # faster than tracking it per matchup would

_event_view_cache = {}
_new_cache_fetches = 0


def load_event_view_cache():
    """Loads the cache, silently upgrading any pre-Correct-Score entries
    (plain 'home'/'away' strings, from before game-1 EXACT SCORES were
    also cached) into the current {'winner':..., 'score': [h,a] or None}
    shape, so downstream code only ever has to deal with one format."""
    global _event_view_cache
    if os.path.exists(EVENT_VIEW_CACHE_PATH):
        try:
            with open(EVENT_VIEW_CACHE_PATH) as f:
                raw = json.load(f)
            _event_view_cache = {
                k: ({"winner": v, "score": None} if isinstance(v, str) else v)
                for k, v in raw.items()
            }
        except Exception as e:
            print(f"  [!] couldn't read event_view_cache.json ({e}) -- starting empty")
            _event_view_cache = {}
    return _event_view_cache


def save_event_view_cache():
    os.makedirs(os.path.dirname(EVENT_VIEW_CACHE_PATH), exist_ok=True)
    with open(EVENT_VIEW_CACHE_PATH, "w") as f:
        json.dump(_event_view_cache, f)


def _fetch_and_cache_game1(event_id):
    """Shared cache/fetch path for everything game-1-related (1st Game
    Winner AND 1st Game Correct Score) -- using the local cache first and
    only calling event/view on a cache miss (and only while under this
    run's MAX_NEW_CACHE_FETCHES_PER_RUN budget), so a matchup that needs
    both the winner and the exact score only costs one lookup, not two.
    Returns {'winner': 'home'/'away', 'score': [h, a]} or None if not
    cached, the budget's used up, the match isn't actually finished yet,
    or the response can't be parsed -- callers should just skip that
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

    info = {"winner": "home" if h > a else "away", "score": [h, a]}
    _event_view_cache[key] = info  # finished result never changes -- cache forever
    return info


def get_game1_result(event_id):
    """Returns 'home' or 'away' -- who won game 1 of this specific,
    already-FINISHED past match. See _fetch_and_cache_game1 for the
    cache/fetch/budget mechanics this sits on top of."""
    info = _fetch_and_cache_game1(event_id)
    return info["winner"] if info else None


def get_game1_score(event_id):
    """Returns (home_points, away_points) for game 1 of this specific,
    already-FINISHED past match, or None if unknown -- either because
    the match itself can't be resolved (see _fetch_and_cache_game1), or
    because it's a pre-Correct-Score cache entry that only ever recorded
    the winner, not the exact score (see load_event_view_cache's
    upgrade step; those entries stay score-less forever since a finished
    result is never re-fetched)."""
    info = _fetch_and_cache_game1(event_id)
    if not info or info.get("score") is None:
        return None
    return tuple(info["score"])


def game1_scoreline_distribution():
    """Pooled (not per-matchup) frequency of every known game-1 exact
    scoreline across ALL cached finished events, regardless of league or
    player -- {(winner_points, loser_points): fraction}. Pooling is the
    point: a game-to-11 (win-by-2) scoreline is a shared physical pattern
    (11-9, 11-7, 11-5, 12-10, ...), not something that varies enough by
    player to need its own per-matchup sample, so this reaches a usable
    size far faster than GAME1_MIN_SAMPLES-per-player ever could. Returns
    ({}, n) when fewer than GAME1_SCORE_MIN_SAMPLES are cached -- n is
    the sample size either way, for the detail text."""
    pairs = [tuple(v["score"]) for v in _event_view_cache.values()
             if isinstance(v, dict) and v.get("score")]
    if len(pairs) < GAME1_SCORE_MIN_SAMPLES:
        return {}, len(pairs)
    counts = {}
    for h, a in pairs:
        key = (h, a) if h > a else (a, h)  # (winner_points, loser_points)
        counts[key] = counts.get(key, 0) + 1
    total = len(pairs)
    return {k: v / total for k, v in counts.items()}, total


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


def race_scoreline_probs(p, n=MATCH_GAMES_TO_WIN):
    """Every exact scoreline probability for a race-to-n match, given an
    independent per-game win probability p. Each is just one term of
    race_to_n_prob()'s own sum -- e.g. for n=3 (CONFIRMED MATCH_GAMES_TO_WIN
    via event/view's "extra":{"bestofsets":"5"}): {(3,0): p^3,
    (3,1): 3*p^3*(1-p), (3,2): 6*p^3*(1-p)^2}. This is what Game Handicap
    is built on below -- the only line that means anything in a race-to-3
    format is -1.5/+1.5 games, i.e. whether the match ends 2-clear (3-0 or
    3-1) rather than going the distance (3-2)."""
    return {(n, k): math.comb(n - 1 + k, k) * (p ** n) * ((1 - p) ** k) for k in range(n)}


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
        match (games won + games lost). Computed but currently UNUSED by
        any active market -- it fed the Total Games market, removed
        2026-10-04 (bet365 doesn't actually offer it for these leagues).
        Left in place since it's cheap to compute and harmless to keep
        around. (Originally scoped as a points-based "Total Points"
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


LOCAL_TZ = ZoneInfo("Europe/London")


def format_kickoff(epoch_str):
    """BetsAPI's "time" field is a unix-epoch string (UTC, per CONFIRMED
    event/history/view samples). Rendered in Europe/London local time
    (handles BST/GMT automatically) since that's the only person using
    this tool -- returns "--:--" on anything unparseable rather than
    raising, since a bad/missing time shouldn't break the whole slate."""
    try:
        dt = datetime.fromtimestamp(int(epoch_str), tz=timezone.utc).astimezone(LOCAL_TZ)
        return dt.strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return "--:--"


def render_match_card(league_name, home_name, away_name, p_home_game, p_away_game,
                       p_home_match, p_away_match,
                       home_proj, away_proj, kickoff="--:--",
                       p_home_cover=None, p_away_cover=None,
                       home_scorelines=None, away_scorelines=None,
                       card_id=0):
    home_hist = "/".join(home_proj["results"][-5:]) or "-"
    away_hist = "/".join(away_proj["results"][-5:]) or "-"

    handicap_row = ""
    if p_home_cover is not None and p_away_cover is not None:
        handicap_row = f"""<p style="margin:6px 0 0 0;color:var(--sub);font-size:13px">Game Handicap -1.5: {away_name} {p_away_cover*100:.0f}% · {home_name} {p_home_cover*100:.0f}%</p>"""

    score_row = ""
    if home_scorelines and away_scorelines:
        combined = (
            [(f"{home_name} {gf}-{ga}", pr) for (gf, ga), pr in home_scorelines.items()]
            + [(f"{away_name} {gf}-{ga}", pr) for (gf, ga), pr in away_scorelines.items()]
        )
        top2 = sorted(combined, key=lambda kv: -kv[1])[:2]
        top2_str = " · ".join(f"{label} ({pr*100:.0f}%)" for label, pr in top2)
        score_row = f"""<p style="margin:4px 0 0 0;color:var(--sub);font-size:13px">Most likely scores: {top2_str}</p>"""

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

    odds_row = f"""<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--panel2)">
      <div style="font-size:11px;color:var(--sub);margin-bottom:4px">Compare vs bet365 (optional, type in current odds)</div>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="b365a-{card_id}" type="number" step="0.01" min="1.01" placeholder="{away_name} odds"
          oninput="spinCompareOdds({card_id}, {p_home_match}, {p_away_match})"
          style="width:100%;padding:6px 8px;border-radius:6px;border:1px solid var(--panel2);background:var(--panel);color:var(--text);font-size:13px">
        <input id="b365h-{card_id}" type="number" step="0.01" min="1.01" placeholder="{home_name} odds"
          oninput="spinCompareOdds({card_id}, {p_home_match}, {p_away_match})"
          style="width:100%;padding:6px 8px;border-radius:6px;border:1px solid var(--panel2);background:var(--panel);color:var(--text);font-size:13px">
      </div>
      <div id="oddsOut-{card_id}"></div>
    </div>"""

    return f"""<div class="builderPanel">
      <div style="font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:.03em">{league_name} · {kickoff}</div>
      <h3 style="margin:2px 0 4px 0;font-size:17px">{away_name} vs {home_name}</h3>
      <p style="margin:0;color:var(--sub);font-size:13px">Per-game win rate: {away_name} {p_away_game*100:.0f}% · {home_name} {p_home_game*100:.0f}%</p>
      {win_bar}
      {handicap_row}
      {score_row}
      {odds_row}
    </div>"""


def build_legs_and_cards(target_date):
    """Returns (legs, cards_html, streak_entries). legs feeds the Safest
    Bet Builder; cards_html is the per-match card; streak_entries feeds
    the Win Streak panel. Mirrors euro_ice.py's build_legs_and_cards()
    shape so the rest of the suite's conventions (results tracker
    wiring, HTML template, builder JS) all carry over unchanged.

    Fixtures are gathered across ALL leagues first, then sorted by
    kickoff time before any legs/cards are built -- so the output (card
    order, and each leg's "match" label, which is prefixed with the
    local kickoff time) reads chronologically across the whole day's
    slate instead of grouped strictly league-by-league."""
    all_matches = []
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
            all_matches.append((league, m))

    def _epoch(pair):
        try:
            return int(pair[1].get("time") or 0)
        except (TypeError, ValueError):
            return 0

    all_matches.sort(key=_epoch)

    legs = []
    cards = ""
    streak_entries = []
    card_idx = 0

    for league, m in all_matches:
            home, away = m.get("home", {}), m.get("away", {})
            home_name, away_name = home.get("name", "Home"), away.get("name", "Away")
            event_id = m.get("id")
            match_date = m.get("time", "")
            kickoff = format_kickoff(match_date)
            match_label = f"{kickoff} {away_name} vs {home_name}"

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
                # --- Game Handicap (-1.5 / +1.5) -----------------------------
                # The only meaningful line in a race-to-3 format -- does the
                # match end 2-clear (3-0/3-1) or go the distance (3-2)? Built
                # directly on the same per-game win rate as Match Winner
                # (no new API calls, no new cache), so it carries the same
                # confidence as the suite's best-performing existing market.
                home_scorelines = race_scoreline_probs(p_home_game)
                away_scorelines = race_scoreline_probs(p_away_game)
                p_home_cover = sum(pr for (gf, ga), pr in home_scorelines.items() if gf - ga >= 2)
                p_away_cover = sum(pr for (gf, ga), pr in away_scorelines.items() if gf - ga >= 2)

                for name, p_cover, opp in (
                    (home_name, p_home_cover, away_name),
                    (away_name, p_away_cover, home_name),
                ):
                    legs.append({
                        "match": match_label, "subject": name,
                        "market": f"{name} -1.5 Games Handicap",
                        "prob": round(p_cover * 100),
                        "hit_rate": None,
                        "category": f"{league['name']} Game Handicap",
                        "detail": f"needs to win 3-0 or 3-1 vs {opp} (not a 3-2 decider)",
                        "history": None,
                        "event_id": event_id, "league_name": league["name"],
                        "home_name": home_name, "away_name": away_name, "match_date": match_date,
                    })

                # --- Correct Score -------------------------------------------
                # The exact scoreline, reusing the same home_scorelines /
                # away_scorelines dicts already computed above for Game
                # Handicap -- no new math, no new API calls. Each race-to-3
                # match can only end 3-0, 3-1 or 3-2 for either player, so
                # this is 6 legs per match (3 scorelines x 2 possible winners).
                for name, scorelines, opp in (
                    (home_name, home_scorelines, away_name),
                    (away_name, away_scorelines, home_name),
                ):
                    for (gf, ga), pr in sorted(scorelines.items(), key=lambda kv: -kv[1]):
                        legs.append({
                            "match": match_label, "subject": name,
                            "market": f"{name} to win {gf}-{ga}",
                            "prob": round(pr * 100),
                            "hit_rate": None,
                            "category": f"{league['name']} Correct Score",
                            "detail": f"exact scoreline vs {opp}",
                            "history": None,
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

                # --- 1st Game Correct Score -----------------------------
                # Exact game-1 scoreline -- each player's own win rate
                # (above) times the POOLED scoreline distribution (see
                # game1_scoreline_distribution's own docstring for why
                # pooling, not per-matchup, is the right sample here).
                # Gated separately from 1st Game Winner: needs both a
                # matchup-level win rate AND enough pooled score samples,
                # so this lags behind 1st Game Winner appearing at all.
                score_dist, score_n = game1_scoreline_distribution()
                if score_dist:
                    top_scores = sorted(score_dist.items(), key=lambda kv: -kv[1])[:3]
                    for name, p_g1, opp in (
                        (home_name, p_home_g1, away_name),
                        (away_name, p_away_g1, home_name),
                    ):
                        for (ws, ls), frac in top_scores:
                            legs.append({
                                "match": match_label, "subject": name,
                                "market": f"{name} to win Game 1 {ws}-{ls}",
                                "prob": round(p_g1 * frac * 100),
                                "hit_rate": None,
                                "category": f"{league['name']} 1st Game Correct Score",
                                "detail": f"game 1 ends {ws}-{ls} in {frac*100:.0f}% of {score_n} cached game-1 results (any player) vs {opp}",
                                "history": None,
                                "event_id": event_id, "league_name": league["name"],
                                "home_name": home_name, "away_name": away_name, "match_date": match_date,
                            })

            # NOTE (2026-10-04): Player Game Total and Game Total (both
            # Total Games markets) were REMOVED here -- user-confirmed
            # these aren't actual bet365 markets for these leagues, so
            # there's nothing to act on. Pulled from the bet builder, the
            # per-match card header, and the results tracker alike; see
            # this file's git history for the removed Poisson-pricing
            # code (prob_over/safe_line/hit_rate are now unused by this
            # module but left in place in case a real market needs them).
            if p_home_match is not None:
                cards += render_match_card(
                    league["name"], home_name, away_name, p_home_game, p_away_game,
                    p_home_match, p_away_match, home_proj, away_proj,
                    kickoff=kickoff,
                    p_home_cover=p_home_cover, p_away_cover=p_away_cover,
                    home_scorelines=home_scorelines, away_scorelines=away_scorelines,
                    card_id=card_idx,
                )
                card_idx += 1

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
    informational, not blended into any projection above.
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
  <h1>🏓 Spin Line — Match Winner, Handicap &amp; Correct Score</h1>
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
      Build. Caps at 2 legs per player/matchup and 2 legs per tournament, so the slip isn't
      stacked on one player or one studio league's reshuffling/bad night, and lists the built
      legs in kickoff order. Tap Shuffle for a fresh pick without changing your settings.
    </div>
  </div>

  {streak_panel}

  {cards}

  <div class="footnote">
    Match Winner uses each player's recency-weighted per-GAME win rate, combined via log5 into
    a per-game probability, then priced as a race-to-{games_to_win} match using the standard
    combinatorial formula. Game Handicap (-1.5/+1.5) uses that exact same per-game probability,
    just asking whether the match ends 2-clear (3-0/3-1) instead of going the distance
    (3-2) -- the only meaningful handicap line in a race-to-3 format. Correct Score breaks that
    same race-to-3 math down into each individual exact scoreline (3-0/3-1/3-2 for either
    player) -- no new computation, just the full breakdown Game Handicap only partially summed.
    1st Game Winner uses
    each player's own recency-weighted rate of winning game 1 of their matches, combined via
    log5 — coverage builds up gradually over time via a local cache (one extra lookup per past
    match, capped per run), so it won't appear for every matchup yet. 1st Game Correct Score
    takes that same per-player game-1 win rate and multiplies it by a POOLED scoreline
    distribution (every cached game-1 exact score, across all players and leagues, not just this
    matchup) — a game-to-11 win-by-2 scoreline is a shared physical pattern, so pooling reaches a
    usable sample much faster than tracking it per matchup; it lags behind 1st Game Winner
    appearing at all, since it needs its own larger pooled sample on top of that. Win Streak is
    informational only — genuinely consecutive match wins, not blended into any projection.
  </div>

<script>
const LEGS = {legs_json};

// Manual bet365-odds comparison (Match Winner only). The model never sees
// real bookmaker odds -- the user types in bet365's current price here so
// they can eyeball the model's probability against the de-vigged market
// probability, per match, without Spin Line trying to auto-adjust anything.
// User's chosen straight-win hunting ground, confirmed 2026-10-05 after a
// 97%-model/coin-flip-market case (Theodor vs Branny) came in as a near-even
// 3-setter: stick to short-ish favorites bet365 itself already trusts,
// rather than the model's own (occasionally overconfident) long-shot calls.
const TARGET_ODDS_MIN = 1.4;
const TARGET_ODDS_MAX = 1.6;
const AGREE_THRESHOLD_PP = 15; // model vs market gap, in points, still counted as "agrees"

function spinCompareOdds(id, pHomeModel, pAwayModel) {{
  const hEl = document.getElementById('b365h-' + id);
  const aEl = document.getElementById('b365a-' + id);
  const out = document.getElementById('oddsOut-' + id);
  const oh = parseFloat(hEl.value);
  const oa = parseFloat(aEl.value);
  if (!oh || !oa || oh <= 1 || oa <= 1) {{
    out.innerHTML = '';
    return;
  }}
  const rawH = 1 / oh, rawA = 1 / oa;
  const overround = rawH + rawA;
  const pHomeMkt = rawH / overround, pAwayMkt = rawA / overround;
  const edgeHome = (pHomeModel - pHomeMkt) * 100;
  const edgeAway = (pAwayModel - pAwayMkt) * 100;
  const fmtEdge = e => (e >= 0 ? '+' : '') + e.toFixed(0) + 'pp';

  function badgeFor(side, odds, pModel, edge) {{
    if (odds < TARGET_ODDS_MIN || odds > TARGET_ODDS_MAX) return '';
    if (pModel <= 0.5) {{
      return '<div style="margin-top:4px;padding:4px 8px;border-radius:6px;background:#3a1420;color:#ff8a93;font-size:12px">'
        + '&#10005; ' + side + ' @' + odds.toFixed(2) + ' in target range, but the model actually favors the other side</div>';
    }}
    if (Math.abs(edge) <= AGREE_THRESHOLD_PP) {{
      return '<div style="margin-top:4px;padding:4px 8px;border-radius:6px;background:#0f3a24;color:#6ee7a8;font-size:12px">'
        + '&#10003; ' + side + ' @' + odds.toFixed(2) + ' -- in target range (' + TARGET_ODDS_MIN + '-' + TARGET_ODDS_MAX + ') and model agrees (' + fmtEdge(edge) + ')</div>';
    }}
    return '<div style="margin-top:4px;padding:4px 8px;border-radius:6px;background:#3a2f0f;color:#f3c969;font-size:12px">'
      + '&#9888; ' + side + ' @' + odds.toFixed(2) + ' in target range, but model disagrees on size (' + fmtEdge(edge) + ')</div>';
  }}

  const badges = badgeFor(aEl.placeholder.replace(' odds', ''), oa, pAwayModel, edgeAway)
    + badgeFor(hEl.placeholder.replace(' odds', ''), oh, pHomeModel, edgeHome);

  out.innerHTML =
    '<div style="margin-top:6px;font-size:12px;color:var(--sub)">' +
    'bet365 implied (de-vigged, ' + (overround * 100 - 100).toFixed(1) + '% margin): ' +
    'away ' + (pAwayMkt * 100).toFixed(0) + '% · home ' + (pHomeMkt * 100).toFixed(0) + '%' +
    '<br>Model vs market edge: away ' + fmtEdge(edgeAway) + ' · home ' + fmtEdge(edgeHome) +
    '</div>' + badges;
}}

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
  const leagueCount = {{}};
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
        // Max 2 legs per tournament, so one league's studio-match
        // reshuffling or a single bad night can't dominate the whole
        // slip -- user request, 2026-10-06, same spirit as the
        // existing per-subject cap above.
        const lgCount = leagueCount[leg.league_name] || 0;
        if (lgCount >= 2) continue;
        chosen.push(leg);
        combinedOdds *= 100 / leg.prob;
        subjectCount[leg.subject] = count + 1;
        leagueCount[leg.league_name] = lgCount + 1;
        addedThisPass = true;
        break;
      }}
    }}
  }}

  // Present the built slip in kickoff order -- easier to work through
  // top to bottom than whatever order categories happened to be
  // round-robin'd in above (user request, 2026-10-06). match_date is
  // the raw BetsAPI epoch string every leg already carries.
  chosen.sort((a, b) => parseInt(a.match_date || 0) - parseInt(b.match_date || 0));

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
