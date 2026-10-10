-- National Trust map: database schema for a NEW Supabase project. Run once in
-- Supabase > SQL Editor. Safe to re-run, which also brings an older project's
-- tables up to date.

-- The place list. Readable only by signed-in users, so the map shows nothing
-- to anyone who is not logged in. Nobody can write it from the browser; it is
-- loaded by tools/sync_supabase.py (the monthly GitHub Action) with the
-- project's secret key, which bypasses these policies.
create table if not exists public.places (
  id     text primary key,
  org    text not null default 'nt',          -- nt = National Trust, nts = NT for Scotland
  name   text not null,
  lat    double precision not null,
  lon    double precision not null,
  cat    text not null,
  descr  text,
  links  jsonb not null default '[]',
  walks  jsonb not null default '[]',
  listed boolean                              -- true = a Trust place to visit; false = only owned by a
);                                            -- Trust, kept because someone has notes on it
alter table public.places add column if not exists org text not null default 'nt';
alter table public.places add column if not exists listed boolean;
alter table public.places enable row level security;
drop policy if exists "signed-in users read places" on public.places;
create policy "signed-in users read places" on public.places
  for select to authenticated using (true);

-- Visited / notes, SHARED by everyone who can sign in (sign-up is
-- switched off, so that is only the accounts made in the dashboard).
-- Deliberately NO foreign key to places: refreshing the place list must never
-- be able to cascade-delete anyone's notes.
create table if not exists public.shared_visits (
  place_id   text primary key,
  visited    boolean not null default false,
  rating     smallint check (rating between 0 and 10),   -- old shared score; the site now uses public.ratings
  notes      text not null default '',
  updated_at timestamptz not null default now(),
  updated_by text                       -- email of whoever last changed it
);
alter table public.shared_visits enable row level security;
drop policy if exists "signed-in users share visits" on public.shared_visits;
create policy "signed-in users share visits" on public.shared_visits
  for all to authenticated using (true) with check (true);

-- Each person's own 0-10 score; the map shows them all and their average.
-- Everyone can read every score, but can only set their own. Clearing a score
-- keeps the row with rating = null. No foreign key, as for shared_visits.
create table if not exists public.ratings (
  place_id   text not null,
  rated_by   text not null,                -- email of whoever gave the score
  rating     smallint check (rating between 0 and 10),
  updated_at timestamptz not null default now(),
  primary key (place_id, rated_by)
);
alter table public.ratings enable row level security;
drop policy if exists "signed-in users read ratings" on public.ratings;
create policy "signed-in users read ratings" on public.ratings
  for select to authenticated using (true);
drop policy if exists "signed-in users set their own rating" on public.ratings;
create policy "signed-in users set their own rating" on public.ratings
  for insert to authenticated with check (rated_by = auth.jwt() ->> 'email');
drop policy if exists "signed-in users change their own rating" on public.ratings;
create policy "signed-in users change their own rating" on public.ratings
  for update to authenticated using (rated_by = auth.jwt() ->> 'email') with check (rated_by = auth.jwt() ->> 'email');

-- Live updates: send changes to open maps straight away.
do $$ begin
  alter publication supabase_realtime add table public.shared_visits;
exception when duplicate_object then null;
end $$;
do $$ begin
  alter publication supabase_realtime add table public.ratings;
exception when duplicate_object then null;
end $$;
