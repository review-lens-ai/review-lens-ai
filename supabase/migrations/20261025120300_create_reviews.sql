-- S1-014: reviews, the normalized review records (rating + text are required).
-- Ownership is resolved through the run and the target. Rows are written by the
-- ingestion pipeline (S1-021/S1-022), never from client input.

create table public.reviews (
    id                uuid        primary key default gen_random_uuid(),
    target_id         uuid        not null,
    run_id            uuid        not null,
    rating            smallint    not null check (rating between 1 and 5),
    review_text       text        not null check (length(btrim(review_text)) > 0),
    -- Optional metadata, kept when the source provides it (S1-BR-021).
    author_name       text,
    reviewed_at       timestamptz,
    source_review_id  text,
    source_url        text,
    source_metadata   jsonb       not null default '{}'::jsonb,
    created_at        timestamptz not null default now(),
    -- Composite foreign key: the run must belong to the SAME target as the
    -- review, so a review can never point at one target and another target's run.
    -- It also guarantees the target exists, so no separate target_id FK is needed.
    constraint reviews_run_target_fkey
        foreign key (run_id, target_id)
        references public.ingestion_runs (id, target_id) on delete cascade,
    -- Foundation for duplicate detection later. Re-ingestion rules are Sprint 2+.
    -- (Rows with NULL source_review_id are never considered duplicates.)
    constraint reviews_target_id_source_review_id_key unique (target_id, source_review_id)
);

create index reviews_target_id_idx on public.reviews (target_id);
create index reviews_run_id_idx on public.reviews (run_id);

alter table public.reviews enable row level security;
revoke all on table public.reviews from anon, authenticated;
