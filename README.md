# National Trust

A private, zoomable map of **National Trust** places (England, Wales and
Northern Ireland) and **National Trust for Scotland** places, whose members get
in free at each other's properties. It's for keeping track of where you've been.

**Live site:** https://newsnoochy.github.io/NationalTrust/ (sign-in required)

- About 830 places. Each pin's **colour** shows whether you've been: brick red
  means not yet, and green with a gold tick means visited. The **white glyph**
  shows what kind of place it is (house, castle, garden, countryside, coast,
  ancient site, mill/industry, abbey/church, museum/other).
- Click a pin to see a one-line description, links (the Trust's page,
  Wikipedia, NT walks, directions), a **Visited** tick, a **0–10 rating** and
  **notes**. Everything saves automatically.
- **Shared:** everyone who can sign in sees and edits the same ticks, ratings
  and notes. A change made on one device shows up live on any other map that's
  open, and each card says who last changed it.
- Search by name, filter by Trust, by type, or by visited/not visited, and use
  "where am I". There's also a satellite view.
- **Keeps itself up to date:** on the 1st of each month, a GitHub Action
  rebuilds the place list from its sources and loads new places into the
  database.

## How the privacy works

GitHub Pages can only serve static files, so sign-in and storage are handled by
[Supabase](https://supabase.com) (free tier):

- **Supabase Auth** handles email and password sign-in. Public sign-up is
  switched off, so only accounts created in the dashboard can get in.
- The place list lives in the `places` table, and row-level security makes it
  readable **only by signed-in users**. It is never committed to this repo
  (`build/` is gitignored), so a visitor who isn't signed in gets a login box
  and nothing else.
- Ticks, ratings and notes live in the `shared_visits` table, which signed-in
  users can read and write. Nobody else can.

The page's code is public (this repo), and so are `config.js`'s URL and
publishable key. That's by design: the key can do nothing that the
row-level-security policies don't allow. The **secret** key, which the monthly
update needs to write places, lives only in the repo's Actions secrets.

## One-time setup (new project)

1. Create a free project at [supabase.com](https://supabase.com). London
   (`eu-west-2`) is the nearest region.
2. **SQL Editor**: paste the contents of
   [`supabase/schema.sql`](supabase/schema.sql) and click Run.
3. **Authentication → Sign In / Providers → Email**: turn **off** "Allow new
   users to sign up".
4. **Authentication → Users → Add user**: enter each person's email and a
   password, and tick "Auto confirm".
5. **Authentication → URL Configuration**: set the Site URL to
   `https://newsnoochy.github.io/NationalTrust/`. The password-reset emails
   link back here.
6. **Project Settings → API Keys**: copy the Project URL and the publishable
   key into [`config.js`](config.js). Create a **secret** key and store it in
   GitHub under **Settings → Secrets and variables → Actions** as
   `SUPABASE_SECRET_KEY`. Put the project URL in
   `.github/workflows/update-places.yml`.
7. **Actions → Update places → Run workflow** loads the places for the first
   time.

A project set up before sharing and Scotland were added needs one run of
[`supabase/migrate_shared_and_scotland.sql`](supabase/migrate_shared_and_scotland.sql).
It merges everyone's existing ticks, ratings and notes into the shared table.

## How the place list is updated

`tools/sync_supabase.py` (run by the Action) rebuilds the list with
`tools/build_places.py` and then:

- upserts every place, so new ones appear and changed details are corrected;
- **keeps place ids stable**, because ticks and notes are keyed by them. A place
  already in the database keeps its id even if the sources reshuffle, and two
  published places are never merged into one;
- refuses to change anything if either Trust's list would shrink by more than
  10%, since that means a source was down, not that places closed;
- removes places the sources no longer list, but only if nobody has a tick,
  rating or note on them.

Each run adds a line of counts to `UPDATES.log`. This also stops GitHub from
switching off the schedule, which it does after 60 days without a commit. To
run it by hand, go to **Actions → Update places → Run workflow**.

To build locally without touching the database:

```
python tools/build_places.py --refresh   # re-download everything
python tools/check_coverage.py           # spot-check well-known places
```

The sources are all open data, and neither Trust's website is scraped, only
linked to:
[Wikidata](https://www.wikidata.org) items owned or run by each Trust (CC0),
the Wikipedia "National Trust properties in …" and "National Trust for
Scotland properties" categories (CC BY-SA), and
[OpenStreetMap](https://www.openstreetmap.org/copyright) features and NT
walking routes with the Trust as operator (ODbL). The walking routes supply
most of the direct NT page links. Nearby parts of one visit are merged into a
single pin (Tyntesfield's chapel and lake go under Tyntesfield; St Kilda's
islands go under St Kilda), with their Wikipedia pages kept on its card. A few
places that no source lists are named by hand in `ORGS` in `build_places.py`.

Not affiliated with the National Trust or the National Trust for Scotland.
