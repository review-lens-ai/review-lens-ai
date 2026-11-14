# Q&A: the tool loop, its contracts and its test harness (S2-010, S2-003)

How a question about the active AnalysisTarget becomes an answer: the model starts with the target's facts, asks for reports from a fixed set of tools, and finishes with `submit_answer`; our code runs every tool, checks every citation and builds the answer. All of it is tested with a fake model, so CI never calls an LLM. The HTTP endpoint (S2-009), the final prompt wording (S2-013) and meaning search are separate PRs.

## One question, step by step

`answer_question` in [`backend/app/services/qa/orchestrator.py`](../../backend/app/services/qa/orchestrator.py):

| Step | What happens | Stops with | Model called? |
|---|---|---|---|
| 1 | Clean the question (trim; blank or over 1000 characters is rejected) | `InvalidQuestionError` (422) | No |
| 2 | Load the target through `TargetRepository.get_owned` | `NotFoundError` (404) for another user's target | No |
| 3 | Read the summary (`ReviewRepository.summary_for_target`, S2-008) | `NoReviewsError` (409, `QA_NO_REVIEWS`) when there are no reviews (S2-BR-025) | No |
| 4 | Messages: `SystemMessage(rules + briefing)`, `HumanMessage(question)`; a new `ToolSet(owner_id, target_id)` | | No |
| 5 | Make the model and run the tool loop | `QAUnavailableError` (503) on a provider failure or missing config; `QAContextError` (500) if a tool returned another target's review | Yes |
| 6 | Build the answer: cited labels → reviews read again from the database | `AnswerResponse` | No |

`owner_id` always comes from the verified token. Every question gets a new `ToolSet`, `LabelBook` and `LoopState`, and reads the reviews again, so nothing carries over between questions or users (S2-BR-023, S2-BR-024). There is no chat history.

## The tool loop

[`backend/app/services/qa/agent.py`](../../backend/app/services/qa/agent.py), `run(model, toolset, messages, max_tool_calls, timeout_s)`. Our code runs the loop; the model only asks.

```
model ──tool call──► toolset.registry.run() ──ToolResult──► ToolMessage "… (4 tool calls left)" ──► model
model ──submit_answer(status, answer, cited, refusal_reason, suggestions)──► build_answer
```

- **The model decides when it has enough**, within a budget: 6 tool calls and 30 seconds (`QA_MAX_TOOL_CALLS`, `QA_TIMEOUT_SECONDS`). Every tool result ends with the calls left. Once the budget or the time is used up, only `submit_answer` is offered (`tool_choice`); a tool asked for after that is not run. The loop always ends after `max_tool_calls + 2` model calls.
- **Tool errors go back to the model** (an unknown tool, bad arguments), so it can retry. A failed call may be repeated; a successful one is not run again with the same arguments.
- **`ToolScopeError` is not caught**: a review from outside the active target is a bug, so it stops the question before anything more is sent.
- **No tool call**: one reminder, then the loop ends. **Invalid `submit_answer`**: one chance to fix it. Either way, giving up returns `None`, which becomes `INSUFFICIENT_EVIDENCE`.

## Contracts

| Contract | File | Used by |
|---|---|---|
| `ChatModel.bind_tools(specs, tool_choice) → .invoke(messages) → AIMessage`; `ModelUnavailableError` | [`app/llm/client.py`](../../backend/app/llm/client.py) | the loop; the fake in tests |
| OpenRouter adapter: `make_chat_model(settings)` | [`app/llm/openrouter.py`](../../backend/app/llm/openrouter.py) | the endpoint (S2-009) |
| `ToolRegistry.specs()` / `.run(name, args) → ToolResult{text, sources, is_error}` | [`app/services/qa/tools/`](../../backend/app/services/qa/tools/) (#118, #119) | the loop |
| `LabelBook.review_id_for(label)` | `tools/labels.py` | the answer builder |
| `SubmitAnswer{status, answer, cited, refusal_reason, suggestions}`, `submit_answer_spec()` | [`app/services/qa/answer.py`](../../backend/app/services/qa/answer.py) | the loop; refusal checks (S2-013) |
| `AnswerResponse{status, answer, evidence, steps, refusal_reason, suggestions, context}` | [`app/schemas/qa.py`](../../backend/app/schemas/qa.py) | the endpoint and UI (S2-009), the eval (S2-004) |

`AnswerStatus` has the three values the model may choose: `ANSWERED`, `INSUFFICIENT_EVIDENCE`, `OUT_OF_SCOPE`. A target with no reviews never gets an answer: it is the 409 above.

## The answer

- **Evidence is read back from the database.** The model cites labels (R1, R2, …); each is mapped through the question's `LabelBook` and the reviews are loaded with `get_many`, which checks the owner. A label no tool handed out is dropped, duplicates are removed, and evidence is capped at 10 (S2-BR-010).
- **No ungrounded answers.** `ANSWERED` with no valid citation, or no text, becomes `INSUFFICIENT_EVIDENCE` with a fixed message and `refusal_reason` `NO_EVIDENCE`.
- **Refusals** keep the model's explanation, its `refusal_reason` and up to 3 suggestions, and never carry evidence (S2-BR-021). The checks on refusal text belong to S2-013.
- **Counts come from the database**, never from the model: the briefing and `context` use the summary and the tools' SQL (techspecs 7.3).

## Context and its limits (S2-010)

- **Tools mode (default).** The model starts with about 1,500 tokens: the rules, the briefing (count, average, stars, dates) and the tool menu. Review text arrives only in tool results: `search_reviews` returns up to 10 excerpts of 300 characters, `get_reviews` up to 10 full texts of 2,000 characters. So a question sends at most a few dozen reviews, whatever the target's size, and counts cover every review because SQL computes them.
- **Each model call re-sends the conversation so far**, so a question with 4 tool calls costs about 5 calls and roughly 11,000 input tokens (estimate, from a 131-character median review).
- **Full mode (fallback, `QA_MODE=full`).** Every review goes in the user message, quoted and labelled, and the model is offered only `submit_answer`. It is used only while the target's review text fits `QA_FULL_MODE_MAX_CHARACTERS` (120,000 characters, about 30,000 tokens); a larger target uses tools mode. Each review is cut at 1,500 characters.
- **Keyword search only.** `search_reviews` matches word stems, not meaning; the prompt tells the model to try two wordings before concluding the reviews don't cover something. Meaning search (embeddings) plugs into `ReviewFilter.meaning` in a later PR.

## The harness

[`backend/tests/unit/qa/conftest.py`](../../backend/tests/unit/qa/conftest.py):

- **`FakeChatModel`** implements `ChatModel`. It records every call (the messages, the tools offered, `tool_choice`) and returns scripted `AIMessage` turns, or raises a scripted error. `call(name, **args)` and `submit(status, …)` build turns.
- **`fake_toolset`** is the real `ToolRegistry` and `LabelBook` with two in-memory tools, so loop and orchestrator tests need no database.

| What it proves | Tests |
|---|---|
| The right instructions | `test_prompt.py`: target (quoted, accents kept), platform, briefing, every rule; review text only in tool results |
| The loop's rules | `test_agent.py`: budget, forced finish, time limit, repeats, errors, `ToolScopeError`, invalid replies, the call limit |
| The answer | `test_answer.py`: labels to stored reviews, unknown labels, cap, downgrade, refusals, suggestions |
| The stops before the model | `test_orchestrator.py`: 422, 404, 409 with no model made; 503 without provider text; full mode |
| Real tools and SQL | `tests/db/test_qa_context_isolation.py`: only the active target's reviews reach the model, User B's phrase never does, switching target changes the tool results, a citation without a tool result gives no evidence |
| Real ingested reviews | `tests/db/ingestion_worker/test_qa_context_from_ingestion.py` |
| The adapter | `tests/unit/llm/test_openrouter.py`, offline |

**Demo (step 7):** `TEST_DATABASE_URL=… uv run pytest tests/db/test_qa_context_isolation.py -k demo -s -v` prints each dataset, the tool calls and results the model received, who owns each review it saw, and each check.

**What the harness cannot prove:** whether a real model follows the prompt. That is the S2-004 evaluation, run live and outside CI.
