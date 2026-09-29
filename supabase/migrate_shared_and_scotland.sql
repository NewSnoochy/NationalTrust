-- One-off upgrade for a database set up with the original schema.sql:
--   * places get an `org` column (nt = National Trust, nts = NT for Scotland)
--   * ticks, ratings and notes become SHARED by everyone who can sign in
--   * shared changes are pushed live to the other person's open map
-- Safe to re-run. The old per-person `visits` table is left untouched as a
-- backup; the site no longer reads it.

alter table public.places add column if not exists org text not null default 'nt';

create table if not exists public.shared_visits (
  place_id   text primary key,
  visited    boolean not null default false,
  rating     smallint check (rating between 0 and 10),
  notes      text not null default '',
  updated_at timestamptz not null default now(),
  updated_by text                       -- email of whoever last changed it
);
alter table public.shared_visits enable row level security;
drop policy if exists "signed-in users share visits" on public.shared_visits;
create policy "signed-in users share visits" on public.shared_visits
  for all to authenticated using (true) with check (true);

-- Carry over what each of you had already recorded: a place counts as visited
-- if either of you ticked it, the most recent rating wins, and both sets of
-- notes are kept (oldest first).
insert into public.shared_visits (place_id, visited, rating, notes, updated_at)
select v.place_id,
       bool_or(v.visited),
       (array_agg(v.rating order by v.updated_at desc) filter (where v.rating is not null))[1],
       coalesce(string_agg(nullif(trim(v.notes), ''), E'\n\n' order by v.updated_at), ''),
       max(v.updated_at)
from public.visits v
group by v.place_id
on conflict (place_id) do nothing;

-- Live updates: send changes to open maps straight away.
do $$ begin
  alter publication supabase_realtime add table public.shared_visits;
exception when duplicate_object then null;
end $$;
