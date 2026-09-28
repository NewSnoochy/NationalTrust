-- National Trust map: database schema. Run once in Supabase > SQL Editor.
-- Safe to re-run.

-- The place list. Readable only by signed-in users, so the map shows nothing
-- to anyone who is not logged in. Nobody can write it from the browser; it is
-- loaded by running build/seed_places.sql in the SQL editor.
create table if not exists public.places (
  id     text primary key,
  name   text not null,
  lat    double precision not null,
  lon    double precision not null,
  cat    text not null,
  descr  text,
  links  jsonb not null default '[]',
  walks  jsonb not null default '[]'
);
alter table public.places enable row level security;
drop policy if exists "signed-in users read places" on public.places;
create policy "signed-in users read places" on public.places
  for select to authenticated using (true);

-- Each user's own visited / rating / notes. Deliberately NO foreign key to
-- places: re-seeding the place list must never be able to cascade-delete
-- anyone's notes.
create table if not exists public.visits (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  place_id   text not null,
  visited    boolean not null default false,
  rating     smallint check (rating between 0 and 10),
  notes      text not null default '',
  updated_at timestamptz not null default now(),
  primary key (user_id, place_id)
);
alter table public.visits enable row level security;
drop policy if exists "own visits" on public.visits;
create policy "own visits" on public.visits
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
