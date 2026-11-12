# Q&A Orchestration and Test Harness (S2-003)

How a question about the active AnalysisTarget becomes a model call, what comes back, and how we test all of it without a live model. Status: the harness is in place. The provider, the context strategy (S2-010) and the final prompt (S2-013) are not decided yet.

## The flow

`answer_question` in [`backend/app/services/qa/orchestrator.py`](../../backend/app/services/qa/orchestrator.py):

| Step | What happens | Stops with | Model called? |
|---|---|---|---|
| 1 | Clean the question (trim; blank or over 1000 characters is rejected) | `InvalidQuestionError` (422) | No |
| 2 | Load the target through `TargetRepository.get_owned` | `NotFoundError` (404) for another user's target | No |
| 3 | Count the target's reviews | `NO_REVIEWS` answer when there are none (S2-BR-025) | No |
| 4 | Select reviews and check every one belongs to the active target | `ContextIsolationError` (500) if one does not; `INSUFFICIENT_EVIDENCE` if none were selected | No |
| 5 | Build the system prompt and the review block, call `ModelClient.complete` | `QAUnavailableError` (503) on any `ModelError` | Yes |
| 6 | Check the reply against the reviews that were sent | `AnswerResponse` | — |

`owner_id` always comes from the verified token. Each call reads the reviews again; nothing is cached, so changing the active target changes the context (S2-BR-023, S2-BR-024).

### Why the check in step 4

The SQL in `ReviewRepository` already filters by target and owner. The check in `build_bundle` is a second, independent guard: S2-010 will add a new query (keyword or vector search), and a missing target filter there would otherwise send another target's reviews to the model. Reviews have no owner column, so the target is what ties a review to its owner.

The system prompt also names the active target, but that is for scope behavior, not security. The model can only leak what it is sent, so isolation is decided entirely by what steps 2 to 4 select.

## The model boundary

[`backend/app/llm/client.py`](../../backend/app/llm/client.py):

```python
ModelRequest(system_prompt, reviews_block, question, history=())
ModelClient.complete(request) -> ModelReply(status, answer, cited_review_ids)
# raises ModelUnavailableError (provider failed or timed out)
#     or ModelReplyError (reply was not in the ModelReply shape)
```

- Reviews go in `reviews_block`, separate from the system prompt, inside `<reviews>` tags. Review text is escaped so it cannot close its own tag, and the prompt says text inside the block is data ([guardrails §7](../standards/data-security-guardrails.md)).
- The real adapter (likely LangChain, in the backend) makes the provider return the three `ModelReply` fields with structured output and raises `ModelReplyError` when it cannot. No adapter exists yet; nothing outside `app/llm/` should import a provider SDK.
- Provider text stays in the exception. The orchestrator logs only the exception type, and users get a fixed `QA_UNAVAILABLE` message.

## The answer contract

[`backend/app/schemas/qa.py`](../../backend/app/schemas/qa.py):

```json
{
  "status": "ANSWERED | INSUFFICIENT_EVIDENCE | OUT_OF_SCOPE | NO_REVIEWS",
  "answer": "text for the user",
  "evidence": [{"id": "...", "rating": 4, "review_text": "...", "reviewed_at": "..."}],
  "context": {"target_id": "...", "target_name": "...", "platform": "google_maps",
              "reviews_considered": 214, "reviews_total": 214, "is_subset": false}
}
```

Rules, in [`answer.py`](../../backend/app/services/qa/answer.py):

- `evidence` is copied from the reviews that were sent. The model returns only ids; unknown ids are dropped, duplicates removed, and the list is capped at 10. So every review shown as evidence really exists in the active dataset (S2-BR-010).
- An `ANSWERED` reply with no valid citation (or no text) is returned as `INSUFFICIENT_EVIDENCE` with a fixed message. This is application logic reinforcing the prompt (S2-BR-020).
- `INSUFFICIENT_EVIDENCE` and `OUT_OF_SCOPE` keep the model's explanation and never carry evidence (S2-BR-021).
- `context` counts come from the database, not the model (techspecs 7.3).
- `NO_REVIEWS` is decided by the app alone.

Statuses map to the S2-004 evaluation categories: in-scope → `ANSWERED`; missing evidence → `INSUFFICIENT_EVIDENCE`; general knowledge, another entity, another platform → `OUT_OF_SCOPE`. That lets a live evaluation check behavior without matching exact sentences.

## The harness

`FakeModel` in [`backend/tests/unit/qa/conftest.py`](../../backend/tests/unit/qa/conftest.py) implements `ModelClient`. It records every `ModelRequest` and returns a scripted `ModelReply` or raises a scripted error. CI never calls a live model.

| S2-003 check | Tests |
|---|---|
| Correct system instructions | `tests/unit/qa/test_prompt.py`: target, platform and each scope rule present; no review text in the system prompt; review block escaped |
| Correct review context | `test_orchestrator.py` and `tests/db/test_qa_context_isolation.py`: the ids sent equal the target's stored review ids |
| No wrong-target reviews | Asking about target A then A2 sends only A2's reviews; a selector that leaks another target's review stops before the model |
| No cross-user reviews | User A's review holds `ZEBRA-PASTA-7`; it never appears in user B's request; B asking about A's target is a 404 with no model call; B cannot get A's review back through a citation |
| Behavior for controlled replies | `test_answer.py`: citations resolved from context, unknown ids dropped, cap, downgrade, refusals, unknown status; `test_orchestrator.py`: provider failure without provider text in the response or the log |
| Context from real ingested reviews | `tests/db/ingestion_worker/test_qa_context_from_ingestion.py`: the real worker ingests the recorded Smashburger pages; the model receives exactly those 18 reviews with their text intact and escaped; another user gets none of it |

The DB tests use the real repositories against a throwaway Postgres (`TEST_DATABASE_URL`), so the ownership SQL itself is what they test.

**Showing the captured context (Sprint 2 demo, step 7).** Two DB tests print the request the model would have received: user A asking about user B's distinctive phrase `MANGO-WAFFLE-9`, and user A switching between two targets. Run them with `-s`:

```bash
cd backend
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/postgres \
  uv run pytest tests/db/test_qa_context_isolation.py -k demo -s -v
```

**What the harness cannot prove:** whether a real model follows the prompt — refuses off-topic questions, admits missing evidence, cites the right reviews. That needs a live run against the real provider, which is S2-004's evaluation set, run outside CI.

## Temporary parts

- **Selector.** `select_newest_reviews` sends the target's newest reviews that fit a size limit (`MAX_CONTEXT_CHARACTERS`, about 30,000 tokens), so every review for most targets and a subset for a very large one. The answer's `context.is_subset` and the prompt say when some were left out. S2-010 replaces it with the chosen strategy and documents the real token limits. Any function with the `ReviewSelector` signature plugs in, and the isolation check and its tests apply unchanged.
- **Prompt wording.** A first draft; S2-013 owns the final text. Tests check rules are present, not exact sentences.

## Planned: persistent chat history

Q&A will keep conversation history; S2-003 ships without it. The design already leaves room:

- `ModelRequest.history` is a tuple of `ChatMessage(role, content)` — the shape LangChain and assistant-ui use — and is always empty today. A test asserts that.
- A conversation will belong to exactly one owner and one target, stored in our own tables through owner-filtered repositories (S2-BR-026), not in a library's or hosted service's storage.
- Switching target starts a new conversation.
- History is AI context too: earlier answers quote reviews. It gets the same isolation tests as reviews (no other user's or other target's turns in a request), and is capped to the last few turns.
- Reviews are still re-selected for every question.
