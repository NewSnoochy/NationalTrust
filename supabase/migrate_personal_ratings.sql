-- One-off upgrade: a score each instead of one shared score. Run once in
-- Supabase > SQL Editor. Safe to re-run.
--
-- Creates public.ratings (see schema.sql), then hands each old shared score
-- to whoever last changed that place (the "Last changed by" name on its
-- card), so nobody is given a score they didn't give. shared_visits.rating is
-- left as it was, as a backup; the site no longer reads it.

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
-- Data API privileges (needed for tables created from 30 Oct 2026; not granted
-- to anon, so scores stay private to signed-in users).
grant select, insert, update on public.ratings to authenticated;
grant all on public.ratings to service_role;

insert into public.ratings (place_id, rated_by, rating, updated_at)
select v.place_id, v.updated_by, v.rating, v.updated_at
from public.shared_visits v
where v.rating is not null and v.updated_by is not null
on conflict (place_id, rated_by) do nothing;

do $$ begin
  alter publication supabase_realtime add table public.ratings;
exception when duplicate_object then null;
end $$;

-- What was carried over: each person's count of scores, and any old score
-- that had nobody to hand it to (those stay only in shared_visits.rating).
select rated_by, count(*) as scores from public.ratings where rating is not null group by rated_by
union all
select '(old score, nobody to give it to)', count(*) from public.shared_visits where rating is not null and updated_by is null;
