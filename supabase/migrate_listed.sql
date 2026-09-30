-- National Trust map: adds places.listed. Run once in Supabase > SQL Editor
-- before the next "Update places" run. Safe to re-run.
--
-- listed = true   a place the Trust opens to visitors (it has a page on the
--                 Trust's website)
-- listed = false  land or a building the Trust only owns; kept on the map
--                 because someone has a tick, rating or note on it
-- listed = null   not checked yet (every row until the next update runs)
alter table public.places add column if not exists listed boolean;
