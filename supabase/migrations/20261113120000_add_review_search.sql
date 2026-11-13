-- Keyword search over review text (S2-010, the Q&A tools).
--
-- search_vector holds each review's words in normalized form (lower case, common
-- words dropped, "slow" and "slowly" reduced to one stem). Postgres fills it from
-- review_text on every insert and update, so it can never disagree with the text
-- and the worker needs no extra step. The GIN index lets a word search jump to the
-- matching reviews instead of reading every row.
--
-- Counts, averages and breakdowns are NOT stored: they are calculated from the
-- reviews each time, so they always match the saved reviews.

alter table public.reviews
    add column search_vector tsvector
        generated always as (to_tsvector('english', review_text)) stored;

create index reviews_search_vector_idx
    on public.reviews using gin (search_vector);
