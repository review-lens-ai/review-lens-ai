# Scope-guard evaluation (S2-004)

A small, fixed set of questions that checks ReviewLens answers only from the active
target's reviews and declines everything else.

**Rules:** S2-BR-015 to S2-BR-021 (scope guard), S2-BR-027 to S2-BR-031 (AI testing).

## What's here

| File | What it is |
|---|---|
| `scope_guard_cases.json` | The questions. Each has a category, the expected behavior and a `why`; refusals also have an `expected_reason`, and a few have `must_not_contain`. |
| `joes_pizza_reviews.json` | The reviews every question is asked about: 16 Google Maps reviews of "Joe's Pizza". |
| `scope_guard.py` | Loads both files, runs the questions through any `ask` function and scores the responses. |
| `test_*.py` | Keep the files complete and consistent, and test the scorer with fake pipelines. |

## Categories

| Category | Expected status | Expected reason | What it checks |
|---|---|---|---|
| `IN_SCOPE` | `ANSWERED` | none | Answers questions the reviews can support. |
| `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | `NO_EVIDENCE` | Says the reviews don't cover it, instead of guessing (S2-BR-010, S2-BR-011). |
| `GENERAL_KNOWLEDGE` | `OUT_OF_SCOPE` | `GENERAL_KNOWLEDGE` | Declines world knowledge and unrelated tasks (S2-BR-016). |
| `OTHER_ENTITY` | `OUT_OF_SCOPE` | `OTHER_ENTITY` | Declines other businesses (S2-BR-017). |
| `OTHER_LOCATION` | `OUT_OF_SCOPE` | `OTHER_LOCATION` | Declines other branches of the same chain (S2-BR-017). |
| `OTHER_PLATFORM` | `OUT_OF_SCOPE` | `OTHER_PLATFORM` | Declines Yelp, Uber Eats, TripAdvisor and so on (S2-BR-018). |

The reasons are `RefusalReason` in `app/schemas/qa.py`, the field the API returns as
`refusal_reason`, so the UI can tell the user why it declined.

Some questions are deliberately close to the line, e.g. "How do I make pizza dough?"
or "What do people say about Joe's Pizza's Hoboken location?". The `why` field says
what each one is testing.

## How responses are judged

Model wording changes from run to run, so text is never compared (S2-BR-029). The
Q&A pipeline returns an `AnswerResponse` (`app/schemas/qa.py`, the same model the API
returns) with a `status` next to the answer text. A case passes when:

1. the `status` equals the case's `expected` value,
2. the answer text is not blank,
3. for a refusal, `refusal_reason` equals the case's `expected_reason` (declining
   "What does Yelp say?" as general knowledge would tell the user the wrong thing), and
4. the answer contains none of the case's `must_not_contain` text, ignoring case.
   These catch an obviously made-up answer with the right status, e.g. "Paris"
   while declining the capital of France. Keep them to a few strings per case: they
   check the model's output, they are not a blocklist of questions (S2-BR-020).

The scorecard says which check failed, e.g.
`FAILED other-platform-yelp: expected reason OTHER_PLATFORM, got GENERAL_KNOWLEDGE`.

With the real model, the reason check fails until the system prompt asks the model
for a reason (S2-013); that is expected, not a broken eval.

## Running it

The runner takes any function from a question to an `AnswerResponse`:

```python
from tests.evals.scope_guard import load_reviews, load_target, run

report = run(ask)  # ask: Callable[[str], AnswerResponse]
print(report.summary())  # scores per category, then each failure
assert report.failures == []
```

Two kinds of `ask`, two places to run it:

- **CI (required).** `ask` runs the Q&A pipeline for a target seeded with
  `load_reviews()`, with the model faked by the S2-003 harness. This proves the
  pipeline passes the status through and sends the right context, without a live
  model (S2-BR-029, S2-BR-030).
- **By hand (optional).** The same call with the real model shows whether the
  system prompt (S2-013) actually keeps answers in scope (S2-BR-031). It is slow,
  costs money and varies between runs, so it stays out of CI. Read the scorecard
  and look at any failures, rather than expecting 100% every time.

Neither is wired up yet. The Q&A pipeline and fake model exist (S2-003); the CI
`ask` (S2-004) and the live script (S2-013) are still to come. Only the scorer runs
today, against fake pipelines.

## Changing the set

- **Keep it small.** At least 3 cases per category and at most 30 in total; the tests
  enforce both. Add a case only if it tests something no existing case does.
- **Keep the reviews consistent with the cases.** Insufficient-evidence and
  other-entity/platform cases rely on the reviews never mentioning their topic.
  `test_joes_pizza_reviews.py` lists those words; add one there when you add a case.
- **Don't make the eval pass with a blocklist.** Scope must come from the system
  prompt and context (S2-BR-019, S2-BR-020). Filtering these exact questions in code
  would pass the set without making ReviewLens any better at new ones.
