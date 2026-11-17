# Q&A: the endpoint, the tool loop, its contracts and its test harness (S2-010, S2-009, S2-013, S2-003)

How a question about the active AnalysisTarget becomes an answer: the model starts with the target's facts, asks for reports from a fixed set of tools, and finishes with `submit_answer`; our code runs every tool, checks every citation and builds the answer. All of it is tested with a fake model, so CI never calls an LLM. Running the scope-guard eval set (S2-004) through this pipeline, in CI and live, is a separate PR.

## The endpoint

`POST /api/v1/targets/{target_id}/questions` with `{"question": "..."}` in [`backend/app/api/questions.py`](../../backend/app/api/questions.py). It needs a verified user (like every `/api/v1` route) and returns an `AnswerResponse`.

| Response | When |
|---|---|
| 200 | an answer, a refusal (`INSUFFICIENT_EVIDENCE`, `OUT_OF_SCOPE`), or `NO_REVIEWS` for a target without reviews |
| 404 | the target is not the caller's |
| 422 | blank question, or over 1000 characters (`INVALID_QUESTION`) |
| 500 | a review from another target reached a tool (`QA_CONTEXT_ERROR`) |
| 503 | the model failed or is not configured (`QA_UNAVAILABLE`); provider text never reaches the user |

Only the question is sent; there is no chat history yet. The model is made through `get_model_factory` only after the checks before it pass, and tests replace that dependency with the fake model.

## One question, step by step

`answer_question` in [`backend/app/services/qa/orchestrator.py`](../../backend/app/services/qa/orchestrator.py):

| Step | What happens | Stops with | Model called? |
|---|---|---|---|
| 1 | Clean the question (trim; blank or over 1000 characters is rejected) | `InvalidQuestionError` (422) | No |
| 2 | Load the target through `TargetRepository.get_owned` | `NotFoundError` (404) for another user's target | No |
| 3 | Read the summary (`ReviewRepository.summary_for_target`, S2-008) | a `NO_REVIEWS` answer when there are no reviews (S2-BR-025) | No |
| 4 | Build the facts (`ContextBundle`: name, platform, address, summary), open the owner-checked search (`ReviewSearchRepository.for_owner`) and a new `ToolSet`; messages: `SystemMessage(scope prompt + mode part)`, `HumanMessage(question)` | | No |
| 5 | Make the model and run the tool loop | `QAUnavailableError` (503) on a provider failure or missing config; `QAContextError` (500) if a tool returned another target's review | Yes |
| 6 | Build the answer: cited labels → reviews read again from the database | `AnswerResponse` | No |

`owner_id` always comes from the verified token. Every question gets a new search, `ToolSet`, `LabelBook` and `LoopState`, and reads the reviews again, so nothing carries over between questions or users (S2-BR-023, S2-BR-024).

## The system prompt (S2-013)

[`prompt.py`](../../backend/app/services/qa/prompt.py) builds it in two parts:

- **`scope_prompt(bundle)`, shared by every mode:** the target (quoted), platform and address (quoted; from `TargetRepository.place_address`), the facts from the saved-review summary (`tools/briefing.py`, the numbers the Summary card shows, S2-BR-007), the rules, the five refusal reasons with the status each goes with, and how to decline.
- **The mode part.** Tools mode: read the reviews through the tools, take counts from the facts or a tool result, try two wordings (keyword search), the tool-call budget, cite labels, finish with `submit_answer`. Full mode: every review is listed in the user message, cite labels, finish with `submit_answer`.

Review text never goes into the system prompt: it arrives in tool results, or in full mode in the user message, each review quoted and flagged by `guard.quote`.

## The tool loop

[`backend/app/services/qa/agent.py`](../../backend/app/services/qa/agent.py), `run(model, toolset, messages, max_tool_calls, timeout_s)`. Our code runs the loop; the model only asks.

```
model ──tool call──► toolset.registry.run() ──ToolResult──► ToolMessage "… (4 tool calls left)" ──► model
model ──submit_answer(status, answer, cited, refusal_reason, suggestions)──► build_answer
```

- **The model decides when it has enough**, within a budget: 6 tool calls and 30 seconds (`QA_MAX_TOOL_CALLS`, `QA_TIMEOUT_SECONDS`). Every tool result ends with the calls left. Once the budget or the time is used up, only `submit_answer` is offered (`tool_choice`); a tool asked for after that is not run. The loop always ends after `max_tool_calls + 2` model calls.
- **Tool errors go back to the model** (an unknown tool, bad arguments), so it can retry. A failed call may be repeated; a successful one is not run again with the same arguments.
- **`ToolScopeError` is not caught**: a review from outside the active target is a bug, so it stops the question before anything more is sent.
- **Timing in the log**: each model call is logged at INFO with its time and the tools it asked for, and each question with its model calls, tool calls and total time. A model failure logs its cause by type (e.g. `RateLimitError`), never the provider's text. Use these to see where a slow answer spends its time.
- **No tool call**: one reminder, then the loop ends. **Invalid `submit_answer`** (including an unknown field): one chance to fix it. Either way, giving up returns `None`, which becomes `INSUFFICIENT_EVIDENCE`.

## Contracts

| Contract | File | Used by |
|---|---|---|
| `ChatModel.bind_tools(specs, tool_choice) → .invoke(messages) → AIMessage`; `ModelUnavailableError`; `ReplyStatus` | [`app/llm/client.py`](../../backend/app/llm/client.py) | the loop; the fake in tests |
| OpenRouter adapter: `make_chat_model(settings)` | [`app/llm/openrouter.py`](../../backend/app/llm/openrouter.py) | the endpoint |
| `ReviewSearchRepository.for_owner(conn, target_id, owner_id)`; `ToolSet(search)` | [`app/repositories/review_search.py`](../../backend/app/repositories/review_search.py), [`app/services/qa/tools/`](../../backend/app/services/qa/tools/) | the orchestrator |
| `ToolRegistry.specs()` / `.run(name, args) → ToolResult{text, sources, is_error}` | `tools/registry.py` | the loop |
| `LabelBook.review_id_for(label)` | `tools/labels.py` | the answer builder |
| `ContextBundle{target_id, target_name, platform, reviews_total, address, summary}` | [`app/services/qa/context.py`](../../backend/app/services/qa/context.py) | the prompt; refusal templates |
| `SubmitAnswer{status, answer, cited, refusal_reason, suggestions}`, `submit_answer_spec()` | [`app/services/qa/answer.py`](../../backend/app/services/qa/answer.py) | the loop |
| `AnswerResponse{status, answer, evidence, steps, refusal_reason, suggestions, context}` | [`app/schemas/qa.py`](../../backend/app/schemas/qa.py) | the endpoint and UI (S2-009), the eval (S2-004) |

`AnswerStatus` has four values. The model may choose three (`ReplyStatus`: `ANSWERED`, `INSUFFICIENT_EVIDENCE`, `OUT_OF_SCOPE`); `NO_REVIEWS` is decided by the app before any model call.

## The answer

- **Evidence is read back from the database.** The model cites labels (R1, R2, …); each is mapped through the question's `LabelBook` and the reviews are loaded with `get_many` from the owner-checked search. A label no tool handed out is dropped, duplicates are removed, and evidence is capped at 10 (S2-BR-010).
- **No ungrounded answers.** `ANSWERED` with no valid citation, or no text, becomes `INSUFFICIENT_EVIDENCE` with a fixed message and `refusal_reason` `NO_EVIDENCE`.
- **Refusals** never carry evidence (S2-BR-021). `INSUFFICIENT_EVIDENCE` always has reason `NO_EVIDENCE`; `OUT_OF_SCOPE` keeps the model's reason only when it is one of the four out-of-scope reasons. A refusal that is blank or longer than `MAX_REFUSAL_CHARACTERS` (500; it has probably answered anyway) is replaced by the fixed template for its reason. Suggestions are trimmed, deduplicated and capped at 2, with one fallback suggestion when the model gave none. These checks read the model's output only, never the user's question, so they are not a blocklist (S2-BR-020).
- **Plain text for the user.** The prompt asks for two to four plain sentences, without Markdown or review labels (labels go only in `cited`; the UI shows the cited reviews under the answer). `tidy_answer` backs it up: it removes bracketed label lists like "(R4, R16)" and `**` markers from the model's text.
- **Counts come from the database**, never from the model: the facts and `context` use the summary and the tools' SQL (techspecs 7.3). `context.reviews_considered` is how many reviews the model was shown.

## Context and its limits (S2-010)

- **Tools mode (default).** The model starts with about 1,500 tokens: the scope prompt, the facts (count, average, stars, dates) and the tool menu. Review text arrives only in tool results: `search_reviews` returns up to 10 excerpts of 300 characters, `get_reviews` up to 10 full texts of 2,000 characters. So a question sends at most a few dozen reviews, whatever the target's size, and counts cover every review because SQL computes them.
- **Each model call re-sends the conversation so far**, so a question with 4 tool calls costs about 5 calls and roughly 11,000 input tokens (estimate, from a 131-character median review).
- **Full mode (fallback, `QA_MODE=full`).** Every review goes in the user message, quoted and labelled, and the model is offered only `submit_answer`. It is used only while the target's review text fits `QA_FULL_MODE_MAX_CHARACTERS` (120,000 characters, about 30,000 tokens); a larger target uses tools mode. Each review is cut at 1,500 characters.

## The harness

[`backend/tests/unit/qa/conftest.py`](../../backend/tests/unit/qa/conftest.py):

- **`FakeChatModel`** implements `ChatModel`. It records every call (the messages, the tools offered, `tool_choice`) and returns scripted `AIMessage` turns, or raises a scripted error. `call(name, **args)` and `submit(status, …)` build turns.
- **`fake_toolset`** is the real `ToolRegistry` and `LabelBook` with two in-memory tools, and **`open_search_with`** opens a search the way `for_owner` does, so loop and orchestrator tests need no database.

| What it proves | Tests |
|---|---|
| The right instructions | `test_prompt.py`: in both modes the target (quoted, accents kept), platform, address, facts, every rule and refusal reason; the tools budget or the full-mode listing |
| The loop's rules | `test_agent.py`: budget, forced finish, time limit, repeats, errors, `ToolScopeError`, invalid replies, the call limit |
| The answer | `test_answer.py`: labels to stored reviews, unknown labels, cap, downgrade, refusal reasons, templates, suggestions; `test_submit_answer.py`: the tool's schema |
| The stops before the model | `test_orchestrator.py`: 422, 404 and `NO_REVIEWS` with no model made; 503 without provider text; full mode |
| The endpoint | `tests/db/test_questions_endpoint.py`: 200 from the target's own reviews, 404, `NO_REVIEWS`, 422, 503 |
| Real tools and SQL | `tests/db/test_qa_context_isolation.py`: only the active target's reviews reach the model, User B's phrase never does, switching target changes the tool results, a citation without a tool result gives no evidence |
| Real ingested reviews | `tests/db/ingestion_worker/test_qa_context_from_ingestion.py` |
| The adapter | `tests/unit/llm/test_openrouter.py`, offline |

**Showing the captured context (Sprint 2 demo, step 7).** Two DB tests print what the model received: user A asking about user B's distinctive phrase, and user A switching between two targets. Run them with `-s`:

```bash
cd backend
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/postgres \
  uv run pytest tests/db/test_qa_context_isolation.py -k demo -s -v
```

**What the harness cannot prove:** whether a real model follows the prompt: refuses off-topic questions, admits missing evidence, cites the right reviews. That needs a live run against the real provider, which is S2-004's evaluation set, run outside CI.

## Known limits

Written down so nobody presents these as solved.

- **The injection flag is not a defence.** Review text shown to the model is quoted and flagged when it matches a short regex ("ignore previous instructions" and similar). A reworded attack passes it. The real boundary is that tools are read-only, run SQL written by us, and are bound to one target whose owner was checked in SQL (`ReviewSearchRepository.for_owner`). A review that convinces the model still cannot make a tool read another user's or target's data.
- **Keyword search is English-only.** `search_vector` uses the `english` configuration, so stems of other languages don't match ("lento" won't find "lenta").
- **The `search_vector` migration must reach Supabase.** It adds a stored generated column, which rewrites the `reviews` table once and locks it briefly. Fine at our size, but keyword tools fail on any database where it hasn't run.
- **Meaning search is for finding reviews, not for counting.** `search_reviews` has a `similar_to` phrase: it embeds the phrase (the same BAAI/bge-small-en-v1.5 model that embeds reviews at ingestion, #121), ranks the target's reviews by closeness with pgvector's `<=>`, and returns review text like any other search. The numbers never reach the model. Counts, averages and terms stay exact SQL, because a count by similar meaning would depend on a closeness threshold. Reviews without an embedding yet are left out. If the model cannot load or the embedder fails, the tool says so and the model uses keywords. The first use in a server process loads the model (about 50 MB, cached in `backend/.cache/fastembed`).
- **`reviews_over_time` covers the latest 24 months** that have reviews and says so when it leaves older months out.

## Planned: persistent chat history

Q&A will keep conversation history; Sprint 2 ships without it. The design already leaves room:

- A conversation will belong to exactly one owner and one target, stored in our own tables through owner-filtered repositories (S2-BR-026), not in a library's or hosted service's storage.
- Earlier turns would be added to the messages before the question, as LangChain `HumanMessage` / `AIMessage`, capped to the last few turns.
- Switching target starts a new conversation.
- History is AI context too: earlier answers quote reviews. It gets the same isolation tests as reviews (no other user's or other target's turns in a request).
- Reviews are still read through the tools for every question.
