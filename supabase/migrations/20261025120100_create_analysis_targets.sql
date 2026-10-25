-- S1-014: analysis_targets, the entity whose reviews are being analyzed.
-- Ownership: owner_id is set by the backend from the verified JWT "sub" claim,
-- never from request input (S1-BR-010). RLS rationale: see the first migration.

-- Shared helper: keeps updated_at current on every UPDATE.
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create table public.analysis_targets (
    id          uuid        primary key default gen_random_uuid(),
    -- Deleting a user deletes their targets (and, via cascades, all child data).
    owner_id    uuid        not null references public.users (id) on delete cascade,
    name        text        not null check (length(btrim(name)) > 0),
    -- Validated by the application (backend/app/platforms.py). Deliberately no
    -- CHECK here: the team supports exactly one platform, and changing it
    -- should not require a schema migration.
    platform    text        not null check (length(btrim(platform)) > 0),
    source_url  text        not null check (length(btrim(source_url)) > 0),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- Every user-facing query is "WHERE owner_id = <verified user>".
create index analysis_targets_owner_id_idx on public.analysis_targets (owner_id);

create trigger analysis_targets_set_updated_at
    before update on public.analysis_targets
    for each row execute function public.set_updated_at();

alter table public.analysis_targets enable row level security;
revoke all on table public.analysis_targets from anon, authenticated;
