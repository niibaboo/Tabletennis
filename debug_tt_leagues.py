"""
Spin Line league-id + response-shape confirmation tool.
--------------------------------------------------------------
Everything in spin_line.py was built against BetsAPI's documentation,
not a live key (BetsAPI is paid, ~$10/mo, no free tier). This script
is the equivalent of debug_leagues.py's --confirm mode for that
tool -- run it once you have a real BETSAPI_TOKEN, BEFORE trusting
spin_line.py's output, to:

  1. Find the real league_ids for every target league (only Setka Cup
     and Czech Liga Pro have a candidate id right now, and even those
     were found via betsapi.com's own page URLs, not a live call --
     see spin_line.py's module docstring, assumption 1).
  2. Dump one real event/history and event/view response so the
     "ss" / "scores" field-shape assumptions in spin_line.py and
     spin_line_results_tracker.py can be corrected against reality
     instead of guesses.
  3. Dump a FINISHED match's event/view response specifically to check
     its "timeline" field -- the one sample pulled so far was a
     pre-match event (time_status "0"), so "timeline" came back empty.
     A finished match might carry real set-by-set sequence data there,
     which would be the only way to build a "1st Game Winner" market
     (event/history's past-event entries only have the final score,
     with no way to tell who won game 1 specifically).

/v3/league has NO name-search of its own (confirmed from its docs) --
it only pages through EVERY league for a sport via max_id, so finding
"Setka Cup" etc. means paginating the whole table-tennis league list
and filtering by name client-side. That's exactly what --confirm does.

Usage:
    export BETSAPI_TOKEN=your-real-token-here

    # Page through every table-tennis league BetsAPI knows about and
    # print any whose name matches one of our 4 targets (case-insensitive
    # substring match, so close variants still show up):
    python3 debug_tt_leagues.py --confirm

    # Dump one real event/history + event/view response for a specific
    # league_id, to a local JSON file, once you know a real league_id:
    python3 debug_tt_leagues.py --dump-sample 22307
    python3 debug_tt_leagues.py --dump-sample 22307 2026-10-05

    # Dump event/view for an already-FINISHED match (checks "timeline"
    # for real set-by-set data, needed for a possible 1st Game Winner
    # market -- see item 3 above):
    python3 debug_tt_leagues.py --dump-finished-sample 22307
    python3 debug_tt_leagues.py --dump-finished-sample 22307 2026-10-02
"""

import os
import sys
import json
import time
import requests

# BetsAPI's own docs (betsapi.com/docs, Introduction page) name a SECOND
# load-balancer domain specifically "in case you have issues with
# api.b365api.com" -- which is exactly the symptom hit here (consistent
# ReadTimeouts from GitHub Actions even with a 30s timeout and 3
# retries, which looks more like a host/IP-range issue than one slow
# response). Both hosts are tried every cycle, in order, before this
# backs off and tries the whole cycle again.
HOSTS = ["https://api.b365api.com", "https://api.betsapi.com"]
SPORT_ID = 92
TOKEN = os.environ.get("BETSAPI_TOKEN")

TARGET_NAMES = ["setka", "tt cup", "czech liga pro", "tt elite"]


def _get(version, path, params=None, cycles=4, timeout=20):
    """version is 'v1' or 'v3'. Tries every host in HOSTS before sleeping
    and retrying the whole cycle, so a host-specific block doesn't waste
    all the retries hammering the one host that's actually the problem.

    Retries both plain timeouts AND transient 5xx responses (502/503/504
    -- confirmed from a live run: most pages succeeded fine across both
    hosts, with a couple of recovered timeouts, but it died outright on
    an unhandled 502 Bad Gateway partway through pagination. A 502 isn't
    a real failure the way a 4xx is -- it's the upstream gateway having a
    bad moment -- so it gets the same retry treatment as a timeout. 4xx
    responses (bad token, bad params) are NOT retried -- those won't fix
    themselves by waiting, and raise_for_status() surfaces them immediately."""
    p = dict(params or {})
    p["token"] = TOKEN
    last_err = None
    for cycle in range(1, cycles + 1):
        for host in HOSTS:
            url = f"{host}/{version}{path}"
            try:
                r = requests.get(url, params=p, timeout=timeout)
                if r.status_code >= 500:
                    last_err = requests.exceptions.HTTPError(f"{r.status_code} from {host}")
                    print(f"    [!] {r.status_code} (transient) on {host} (cycle {cycle}/{cycles})")
                    continue
                r.raise_for_status()  # raises immediately on a real 4xx, not retried
                print(f"    (served by {host})")
                return r.json()
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                last_err = e
                print(f"    [!] {type(e).__name__} on {host} (cycle {cycle}/{cycles})")
        if cycle < cycles:
            time.sleep(min(5 * cycle, 20))
    raise last_err


def confirm_leagues():
    print("Paginating every table-tennis league via /v3/league (sport_id=92)...\n")
    all_leagues = []
    max_id = None
    page = 0
    while True:
        page += 1
        params = {"sport_id": SPORT_ID}
        if max_id:
            params["max_id"] = max_id
        try:
            data = _get("v3", "/league", params)
        except Exception as e:
            # Don't let one unlucky page after retries throw away every
            # league already found on earlier pages -- print what we
            # have and let the caller decide whether to re-run for the
            # rest (this is a debug tool, not the production model).
            print(f"  [!] page {page} failed after all retries ({e}) -- "
                  f"stopping here with {len(all_leagues)} leagues collected so far. "
                  f"Re-run to continue; max_id={max_id} is where it stopped.")
            break
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            break
        all_leagues.extend(results)
        print(f"  page {page}: {len(results)} leagues (running total {len(all_leagues)})")
        # Pagination cursor convention is unconfirmed too -- try the
        # lowest id seen minus 1, a common BetsAPI max_id pattern. If
        # this loops forever or stops too early, that's the first thing
        # to check against the real /v3/league docs response.
        ids = [int(lg["id"]) for lg in results if str(lg.get("id", "")).isdigit()]
        if not ids:
            break
        new_max_id = min(ids) - 1
        if max_id is not None and new_max_id >= max_id:
            break
        max_id = new_max_id
        if page > 50:  # hard safety stop, not a real expected page count
            print("  [!] stopped after 50 pages as a safety limit")
            break

    print(f"\n{len(all_leagues)} total table-tennis leagues found.\n")
    print("Matches against our 4 target leagues:\n")
    for lg in all_leagues:
        name = str(lg.get("name", ""))
        if any(t in name.lower() for t in TARGET_NAMES):
            print(f"  id={lg.get('id')}  name={name!r}  cc={lg.get('cc')}")

    print("\nIf a target didn't show up above, try widening TARGET_NAMES in this "
          "file (league names on BetsAPI don't always match bet365's own naming "
          "exactly) and re-run.")


def dump_sample(league_id, day=None):
    import datetime
    day = day or datetime.date.today().strftime("%Y%m%d")

    print(f"Fetching upcoming events for league_id={league_id} day={day}...")
    data = _get("v3", "/events/upcoming", {"sport_id": SPORT_ID, "league_id": league_id, "day": day})
    results = data.get("results", []) if isinstance(data, dict) else []
    print(f"  {len(results)} event(s) found")
    if not results:
        print("  Nothing to sample -- try a different day (BetsAPI 'day' format is YYYYMMDD).")
        return

    out = {"events_upcoming_sample": data}
    event_id = results[0].get("id")
    print(f"Using first event_id={event_id} for history + view samples...")

    history = _get("v1", "/event/history", {"event_id": event_id, "qty": 10})
    out["event_history_sample"] = history

    view = _get("v1", "/event/view", {"event_id": event_id})
    out["event_view_sample"] = view

    path = f"spin_line_sample_{league_id}_{day}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {path} -- send this back so spin_line.py's parsing can be "
          f"corrected against the real field names.")


def dump_finished_sample(league_id, day=None):
    """Finds an already-FINISHED match for this league and dumps its
    event/view response, specifically to check whether "timeline" (empty
    in the one pre-match sample pulled so far) carries real set-by-set
    data for a completed match -- the only way a "1st Game Winner"
    market could be built, since event/history's past-event entries
    only ever have the final score.

    Tries /v3/events/ended (same events family/id-namespace as
    /v3/events/upcoming, per BetsAPI's own convention -- see
    spin_line.py's module docstring) for the given day, then falls back
    to yesterday if nothing's finished yet for today (these are rapid
    studio cups, but "day" here is BetsAPI's own day bucket, not a live
    clock, so a same-day finished match isn't guaranteed to show up
    immediately)."""
    import datetime

    def _try_day(d):
        print(f"Checking /v3/events/ended for league_id={league_id} day={d}...")
        data = _get("v3", "/events/ended", {"sport_id": SPORT_ID, "league_id": league_id, "day": d})
        results = data.get("results", []) if isinstance(data, dict) else []
        finished = [e for e in results if str(e.get("time_status")) == "3"]
        print(f"  {len(results)} event(s), {len(finished)} already finished (time_status=3)")
        return finished

    today = day or datetime.date.today().strftime("%Y%m%d")
    finished = _try_day(today)
    used_day = today
    if not finished:
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")
        print("  Nothing finished for today yet -- trying yesterday instead...")
        finished = _try_day(yesterday)
        used_day = yesterday

    if not finished:
        print("  Still nothing finished -- try a different league_id or day explicitly.")
        return

    event_id = finished[0].get("id")
    print(f"Using finished event_id={event_id}, fetching event/view...")
    view = _get("v1", "/event/view", {"event_id": event_id})

    results = view.get("results", []) if isinstance(view, dict) else []
    event = results[0] if isinstance(results, list) and results else (results if isinstance(results, dict) else {})
    timeline = event.get("timeline")
    print(f"\n'timeline' field: {'present, ' + str(len(timeline)) + ' entries' if isinstance(timeline, list) else repr(timeline)}")
    if isinstance(timeline, list) and timeline:
        print("First couple entries:")
        for t in timeline[:3]:
            print(f"  {t}")

    out = {"finished_event_meta": finished[0], "event_view_finished_sample": view}
    path = f"spin_line_finished_sample_{league_id}_{used_day}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {path} -- send this back so we can tell whether a 1st Game Winner "
          f"market is buildable from 'timeline' or not.")


if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: BETSAPI_TOKEN is not set in this shell.")
        print("Run: export BETSAPI_TOKEN=your-real-token-here")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--confirm":
        confirm_leagues()
    elif len(sys.argv) > 1 and sys.argv[1] == "--dump-sample":
        if len(sys.argv) < 3:
            print("Usage: python3 debug_tt_leagues.py --dump-sample <league_id> [YYYYMMDD]")
            sys.exit(1)
        dump_sample(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif len(sys.argv) > 1 and sys.argv[1] == "--dump-finished-sample":
        if len(sys.argv) < 3:
            print("Usage: python3 debug_tt_leagues.py --dump-finished-sample <league_id> [YYYYMMDD]")
            sys.exit(1)
        dump_finished_sample(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        print(__doc__)
