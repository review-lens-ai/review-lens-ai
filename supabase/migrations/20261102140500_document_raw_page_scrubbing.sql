-- ingestion_raw_pages is what lets a retry re-run NORMALIZATION without paying the
-- provider again. Raw SerpApi pages name every reviewer, so a page is stored only
-- after the adapter's identity scrub (the same one normalize_reviews.py applies to
-- rejected records): no user name, link, thumbnail, contributor id, images or owner
-- response.

comment on column public.ingestion_raw_pages.payload is
    'Provider page AFTER the identity scrub: no reviewer names, links, photos or owner replies.';
