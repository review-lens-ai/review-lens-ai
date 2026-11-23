-- S3-006: questions, each question asked about a target and ReviewLens's response,
-- so the Q&A history is saved with the analysis (S3-BR-009) and can be restored
-- in the order it was asked (S3-BR-010).
--
-- Ownership is through the target, like reviews: there is no owner_id column.
-- QuestionRepository loads the target with get_owned(target_id, owner_id) before
-- reading, writing or clearing these rows. RLS rationale: see the first migration.
--
-- response is the AnswerResponse the user saw, except that evidence holds only the
-- cited review ids ([{"id": ...}], in citation order). The reviews are read back
-- from public.reviews when the history is restored, so review text is not copied
-- here. Review ids stay the same across re-ingestion.

create table public.questions (
    id          uuid        primary key default gen_random_uuid(),
    -- Deleting a target deletes its history. Clearing the history never deletes
    -- the target, its runs or its reviews.
    target_id   uuid        not null references public.analysis_targets (id) on delete cascade,
    question    text        not null check (length(btrim(question)) > 0),
    response    jsonb       not null,
    created_at  timestamptz not null default now(),
    -- Breaks ties in created_at: rows saved in one transaction share now(), and
    -- id is random, so "oldest first" is created_at, then position.
    position    bigint      generated always as identity
);

-- Every query is "WHERE target_id = <owned target>", read oldest first.
create index questions_target_id_position_idx on public.questions (target_id, position);

alter table public.questions enable row level security;
revoke all on table public.questions from anon, authenticated;
