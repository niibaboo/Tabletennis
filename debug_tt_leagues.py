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
"""

import os
import sys
import json
import requests

BASE_V1 = "https://api.b365api.com/v1"
BASE_V3 = "https://api.b365api.com/v3"
SPORT_ID = 92
TOKEN = os.environ.get("BETSAPI_TOKEN")

TARGET_NAMES = ["setka", "tt cup", "czech liga pro", "tt elite"]


def _get(base, path, params=None):
    p = dict(params or {})
    p["token"] = TOKEN
    r = requests.get(f"{base}{path}", params=p, timeout=15)
    r.raise_for_status()
    return r.json()


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
        data = _get(BASE_V3, "/league", params)
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
    data = _get(BASE_V3, "/events/upcoming", {"sport_id": SPORT_ID, "league_id": league_id, "day": day})
    results = data.get("results", []) if isinstance(data, dict) else []
    print(f"  {len(results)} event(s) found")
    if not results:
        print("  Nothing to sample -- try a different day (BetsAPI 'day' format is YYYYMMDD).")
        return

    out = {"events_upcoming_sample": data}
    event_id = results[0].get("id")
    print(f"Using first event_id={event_id} for history + view samples...")

    history = _get(BASE_V1, "/event/history", {"event_id": event_id, "qty": 10})
    out["event_history_sample"] = history

    view = _get(BASE_V1, "/event/view", {"event_id": event_id})
    out["event_view_sample"] = view

    path = f"spin_line_sample_{league_id}_{day}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {path} -- send this back so spin_line.py's parsing can be "
          f"corrected against the real field names.")


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
    else:
        print(__doc__)
