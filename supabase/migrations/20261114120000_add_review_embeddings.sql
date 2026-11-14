-- One embedding per review: its meaning as 384 numbers (BAAI/bge-small-en-v1.5,
-- normalized to length 1), for search by similar meaning.
--
-- null means "not embedded yet". The ingestion worker fills it right after a run
-- saves its reviews; scripts/embed-reviews.sh fills any that are still null
-- (reviews saved before this migration, or a run whose embedding step failed).
-- Both only ever touch rows that are null, so running either again changes nothing.
--
-- No vector index: a search reads one target's reviews (a few hundred), so an
-- exact scan is fast and never misses a match.

create extension if not exists vector;

alter table public.reviews
    add column embedding vector(384);
