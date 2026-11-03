-- S1-014: public.users, the application's mirror of Supabase Auth accounts.
--
-- HOW TO APPLY (never use the Supabase dashboard for schema changes):
--   ./scripts/migrate.sh            apply every pending migration
--   ./scripts/migrate.sh --status   show applied / pending migrations
--
-- ============================================================================
-- ROW LEVEL SECURITY (RLS): HOW ReviewLens USES IT  (read this once)
-- ============================================================================
-- What RLS is
--   A Postgres feature that filters rows per database role/user, e.g. a policy
--   "auth.uid() = owner_id" lets a signed-in user see only their own rows.
--
-- Which Postgres roles exist, and who uses them
--   postgres       The account inside DATABASE_URL. ONLY the FastAPI backend
--                  logs in as this role. It owns the tables and BYPASSES RLS,
--                  so RLS cannot protect rows from the backend. That is why
--                  every backend query MUST filter by owner_id (see
--                  backend/app/repositories/targets.py, get_owned()).
--   anon           Used by Supabase's auto-generated Data API (PostgREST) for
--                  requests that carry only the public publishable key.
--   authenticated  Used by that same Data API when the request carries a
--                  signed-in user's JWT.
--   Our browser code never queries tables (no supabase.from(...)); it only
--   calls FastAPI. The Data API is nevertheless reachable by anyone who has
--   the publishable key, which is public by design.
--
-- Question: "Where would we use the postgres role?"
--   Only in the backend's DATABASE_URL (backend/.env and GitHub Actions
--   secrets). It is never sent to the browser and never put in a NEXT_PUBLIC_
--   variable. It is also the role the migration script uses.
--
-- Question: "For RLS we'd have policies per table for the user id."
--   That is the right pattern WHEN the browser queries tables directly with the
--   user's JWT (policy: auth.uid() = owner_id). We deliberately do not do that:
--   all access goes through FastAPI, which verifies the token and filters by
--   owner. So we turn RLS ON with ZERO policies. In Postgres, RLS enabled with
--   no policy means "deny every row" for roles that do not bypass RLS, so
--   anon/authenticated can read and write nothing, even if a grant is
--   accidentally added later.
--
-- The two layers we apply to EVERY table, in the SAME migration that creates it
--   1. ALTER TABLE ... ENABLE ROW LEVEL SECURITY   (deny-all, no policies)
--   2. REVOKE ALL ... FROM anon, authenticated      (no table privileges)
--   Supabase grants new public tables to anon/authenticated by default, so the
--   REVOKE is required, not optional.
--
--   If a future feature needs the browser to read a table directly, add a
--   narrow policy (and GRANT) for that table in its own reviewed migration.
-- ============================================================================

create table public.users (
    -- Same UUID as auth.users.id, which is the JWT's "sub" claim.
    id          uuid        primary key references auth.users (id) on delete cascade,
    email       text,
    created_at  timestamptz not null default now()
);

-- Copy every new Supabase Auth account into public.users automatically, so
-- analysis_targets.owner_id always has a row to point at.
-- SECURITY DEFINER runs as the function owner so the trigger can write to
-- public.users no matter who signed up; search_path is pinned to '' so a
-- malicious schema cannot hijack object names inside the function.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.users (id, email)
    values (new.id, new.email)
    on conflict (id) do nothing;
    return new;
end;
$$;

-- Only the trigger should run this function, never an API caller.
revoke all on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- Accounts that signed up before this migration (e.g. the shared test users).
insert into public.users (id, email)
select id, email from auth.users
on conflict (id) do nothing;

alter table public.users enable row level security;
revoke all on table public.users from anon, authenticated;
