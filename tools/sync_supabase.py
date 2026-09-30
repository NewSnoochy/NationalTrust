"""Rebuild the place list from its sources and load it into Supabase.

Run by .github/workflows/update-places.yml once a month (or on demand from
the Actions tab). Needs two environment variables:
  SUPABASE_URL          https://<project>.supabase.co
  SUPABASE_SECRET_KEY   the project's secret key (sb_secret_...) or legacy
                        service_role key. It bypasses row-level security, so it
                        lives only in the GitHub repository's secrets.

What it does:
  1. Reads the ids already in `places`, so build_places.py keeps them stable
     (ticks, ratings and notes are keyed by place id).
  2. Rebuilds the list from Wikidata, Wikipedia, OpenStreetMap and the
     Trusts' sitemaps.
  3. Refuses to continue if either Trust's places have shrunk by more than
     10%, which means a source was down or broken, not that places closed.
  4. Upserts every Trust place (new ones appear, changed ones are corrected).
  5. Removes every other place - one the Trust only owns, or one the sources
     no longer list - unless someone has a tick, rating or note on it. Those
     are kept, with listed = false, and the map marks them as not a Trust
     place to visit.

Writes a one-line summary to build/last_update.txt for the workflow to log.
"""
import json, os, sys, urllib.error, urllib.parse, urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_places  # noqa: E402

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SECRET_KEY"]
HEADERS = {"apikey": KEY, "Content-Type": "application/json"}
if KEY.startswith("eyJ"):                 # legacy JWT keys also go in Authorization
    HEADERS["Authorization"] = "Bearer " + KEY
MAX_SHRINK = 0.10


def rest(method, path, body=None, prefer=None):
    headers = dict(HEADERS)
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Supabase {method} {path.split('?')[0]} failed: {e.code} {e.read().decode()[:300]}")


def select_all(table, cols):
    out, step = [], 1000
    while True:
        rows = rest("GET", f"{table}?select={cols}&order={cols.split(',')[0]}&limit={step}&offset={len(out)}")
        out += rows
        if len(rows) < step:
            return out


def main():
    try:
        existing = select_all("places", "id,org,name,listed")
    except SystemExit as e:
        if "listed" in str(e):
            raise SystemExit("The places table has no 'listed' column yet: run supabase/migrate_listed.sql "
                             "in the Supabase SQL Editor first. Nothing was changed.")
        raise
    print(f"{len(existing)} places in the database")
    os.makedirs(build_places.CACHE, exist_ok=True)
    with open(os.path.join(build_places.BUILD, "existing_ids.json"), "w", encoding="utf-8") as f:
        json.dump([r["id"] for r in existing], f)

    build_places.REFRESH = True
    build_places.main()
    with open(os.path.join(build_places.BUILD, "places.json"), encoding="utf-8") as f:
        rows = json.load(f)

    # Only rows already marked listed count: before the first run with the
    # sitemaps every row is unmarked (null), and nothing is compared.
    before = Counter(r["org"] for r in existing if r["listed"])
    after = Counter(r["org"] for r in rows if r["listed"])
    for org, n in before.items():
        if after[org] < n * (1 - MAX_SHRINK):
            raise SystemExit(f"Refusing to update: {org} would shrink from {n} to {after[org]} places. "
                             "A source is probably down; nothing was changed.")

    in_use = {v["place_id"] for v in select_all("shared_visits", "place_id")}
    publish = [r for r in rows if r["listed"] or r["id"] in in_use]
    old_ids = {r["id"] for r in existing}
    new = [r for r in publish if r["id"] not in old_ids]
    for i in range(0, len(publish), 200):
        rest("POST", "places?on_conflict=id", publish[i:i + 200], prefer="resolution=merge-duplicates,return=minimal")
    print(f"upserted {len(publish)} places ({len(new)} new)")
    for r in new:
        print("  new:", r["name"], f"({r['org']})")

    built = {r["id"]: r for r in rows}
    published = {r["id"] for r in publish}
    removable = [r for r in existing if r["id"] not in published and r["id"] not in in_use]
    unlisted = [r for r in publish if not r["listed"]]
    gone = [r for r in existing if r["id"] not in built and r["id"] in in_use]   # no source lists it at all
    for i in range(0, len(removable), 100):
        ids = ",".join(urllib.parse.quote(f'"{r["id"]}"') for r in removable[i:i + 100])
        rest("DELETE", f"places?id=in.({ids})", prefer="return=minimal")
    for i in range(0, len(gone), 100):
        ids = ",".join(urllib.parse.quote(f'"{r["id"]}"') for r in gone[i:i + 100])
        rest("PATCH", f"places?id=in.({ids})", {"listed": False}, prefer="return=minimal")
    for r in removable:
        print("  removed (not a Trust place to visit, nobody's notes on it):", r["name"])
    for r in unlisted + gone:
        print("  kept, marked not a Trust place (has a tick/rating/notes):", r["name"])

    summary = (f"{sum(after.values())} Trust places ({after['nt']} NT, {after['nts']} NTS); "
               f"{len(new)} new, {len(removable)} removed, {len(unlisted) + len(gone)} others kept for their notes")
    with open(os.path.join(build_places.BUILD, "last_update.txt"), "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(summary)


if __name__ == "__main__":
    main()
