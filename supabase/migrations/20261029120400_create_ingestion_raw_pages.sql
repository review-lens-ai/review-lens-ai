-- Ingestion pipeline schema, step 5 of 5: ingestion_raw_pages, the provider's raw responses.
--
-- The ingestion stage saves each page the provider returned BEFORE anything is
-- normalized. If normalization fails, the raw data is still here: it can be
-- diagnosed and re-processed without paying for another provider call.
-- Ownership is resolved through the run and the target.
--
-- DO NOT store credentials here. Save the provider's response body only, never
-- the request URL or headers (they contain the API key).

create table public.ingestion_raw_pages (
    id                uuid        primary key default gen_random_uuid(),
    run_id            uuid        not null references public.ingestion_runs (id) on delete cascade,
    -- 1 for the first page the run fetched, 2 for the next, and so on.
    page_number       integer     not null check (page_number >= 1),
    payload           jsonb       not null check (jsonb_typeof(payload) = 'object'),
    -- Token that fetched the NEXT page, if the provider returned one.
    next_page_token   text,
    created_at        timestamptz not null default now(),
    constraint ingestion_raw_pages_run_id_page_number_key unique (run_id, page_number)
);

create index ingestion_raw_pages_run_id_idx on public.ingestion_raw_pages (run_id);

alter table public.ingestion_raw_pages enable row level security;
revoke all on table public.ingestion_raw_pages from anon, authenticated;
