-- Minimal stand-in for what Supabase provides, so migrations can run on a plain
-- Postgres (local test database, CI). Never applied to a real Supabase project.
do $$
begin
    if not exists (select from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
end
$$;

create schema if not exists auth;
create table if not exists auth.users (
    id    uuid primary key default gen_random_uuid(),
    email text
);
