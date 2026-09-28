# National Trust

A private, zoomable map of National Trust places in England, Wales and Northern
Ireland, for keeping track of where you've been.

**Live site:** https://newsnoochy.github.io/NationalTrust/ (sign-in required)

- About 740 places. Each pin's **colour** shows whether you've been: brick red
  means not yet, and green with a gold tick means visited. The **white glyph**
  shows what kind of place it is (house, castle, garden, countryside, coast,
  ancient site, mill/industry, abbey/church, museum/other).
- Click a pin to see a one-line description, links (the National Trust page,
  Wikipedia, NT walks, directions), a **Visited** tick, a **0–10 rating** and
  your **notes**. Everything saves automatically.
- Search by name, filter by type or by visited/not visited, and use "where am
  I". There's also a satellite view.

## How the privacy works

GitHub Pages can only serve static files, so sign-in and storage are handled by
[Supabase](https://supabase.com) (free tier):

- **Supabase Auth** handles email and password sign-in. Public sign-up is
  switched off, so only accounts created in the dashboard can get in.
- The place list lives in the `places` table, and row-level security makes it
  readable **only by signed-in users**. It is never committed to this repo
  (`build/` is gitignored), so a visitor who isn't signed in gets a login box
  and nothing else.
- Your ticks, ratings and notes live in the `visits` table. Each user can read
  and write **only their own rows**.

The page's code is public (this repo), and so are `config.js`'s URL and anon
key. That's by design: the anon key can do nothing that the row-level-security
policies don't allow.

## One-time setup

1. Create a free project at [supabase.com](https://supabase.com). London
   (`eu-west-2`) is the nearest region.
2. **SQL Editor**: paste and run [`supabase/schema.sql`](supabase/schema.sql).
3. **SQL Editor**: paste and run `build/seed_places.sql`, which
   `python tools/build_places.py` generates. It upserts, so re-running it later
   to refresh the places never touches your notes.
4. **Authentication → Sign In / Providers → Email**: turn **off** "Allow new
   users to sign up".
5. **Authentication → Users → Add user**: enter your email and a password, and
   tick "Auto confirm". Repeat for anyone else who should have access; each
   person gets their own visited list.
6. **Authentication → URL Configuration**: set the Site URL to
   `https://newsnoochy.github.io/NationalTrust/`. The password-reset emails
   link back here.
7. **Project Settings → API**: copy the Project URL and the `anon` `public` key
   into [`config.js`](config.js), then commit.

## Refreshing the place list

```
python tools/build_places.py --refresh   # re-download everything
python tools/check_coverage.py           # spot-check well-known places
```

Then run the new `build/seed_places.sql` in the SQL editor.

The sources are all open data, and the NT website itself is only linked to,
never scraped:
[Wikidata](https://www.wikidata.org) items owned or run by the National Trust
(CC0), the Wikipedia "National Trust properties in …" categories
(CC BY-SA), and [OpenStreetMap](https://www.openstreetmap.org/copyright)
features and NT walking routes with `operator=National Trust` (ODbL). The
walking routes supply most of the direct NT page links. Nearby parts of one
visit are merged into a single pin (Tyntesfield's chapel and lake go under
Tyntesfield), with their Wikipedia pages kept on its card.

Not affiliated with the National Trust.
