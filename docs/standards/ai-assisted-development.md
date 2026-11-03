# AI-Assisted Development

**Story:** S1-004 — Create AI-Assisted Development and Review Context Document

How AI-generated code is prompted, reviewed, tested and approved before merge.

AI-generated code is team-authored code. It follows [Coding Standards](coding-standards.md), [Engineering Philosophy](philosophy.md) and [Data and Security Guardrails](data-security-guardrails.md). AI tools are expected on this project (see the product requirements), and the team uses them to write code, tests and documents. The author of a change is responsible for all of it, whoever or whatever typed it.

## 1. Tools

The team expects to use:

- Claude Code
- Codex

Another AI tool may be used if the author says so in the PR. Every tool follows the rules in this document.

## 2. Context

Give the tools what they need to follow our rules:

- the repository code, tests and migrations;
- the documents in `docs/` (standards, architecture, guardrails);
- the story and its acceptance criteria;
- saved sample data in `backend/tests/fixtures/` (public reviews with reviewer names removed).

Never give the tools:

- `.env` files, API keys, tokens, passwords or connection strings (including `DATABASE_URL`);
- live data, or any real user's data;
- anything from the Supabase or SerpApi dashboards that contains a secret.

## 3. Reviewing generated code

- No one merges AI-generated code they can't explain, verify and maintain. Any team member may be asked to explain any line they merged, in a PR review or in the demo.
- The author reads all of the generated change before opening the PR, and removes anything they don't understand or didn't ask for.
- The reviewer reviews AI-generated code like any other code. An AI review tool may help, but a person approves.
- Generated code that copies a library, a snippet or a document verbatim is checked for its source and licence. If it came from somewhere, say where in the PR. Don't present someone else's work as ours.

## 4. Validating generated dependencies and APIs

AI tools can invent packages, functions, endpoints and options that don't exist.

- Check every suggested dependency and API against the official documentation, and confirm the version exists.
- Run it. Don't merge a call or a library you haven't seen work.
- New dependencies also follow [Coding Standards §7](coding-standards.md#7-dependencies): team approval, an official source linked in the PR, and a justification.

## 5. Testing before accepting generated code

[Coding Standards §8](coding-standards.md#8-testing) is still TBD. Until it is written, generated code is accepted only when:

- the CI check passes (lint, format, types, tests, build; see `scripts/check.sh`);
- the change has tests for its behaviour, including a failing-input test where the story asks for one (authentication, validation, ingestion);
- user-owned data has the two-user tests in [Data and Security Guardrails §8](data-security-guardrails.md#8-testing-cross-user-isolation);
- the author has seen the new tests fail when the behaviour is broken. A test an AI wrote that can't fail proves nothing.

## 6. Session transcripts

The project does not keep AI session transcripts. Each developer's tool keeps its own locally, and they may contain secrets or personal data, so they are not committed.

From Sprint 2, every PR that used AI carries a short "AI use" note: what the AI was used for, and what the author checked or tested. (This is a commitment, not yet current practice: Sprint 1 PRs do not have the note, and `.github/pull_request_template.md` does not have the field yet.)

## 7. Extra security review

A security report is required in the PR when generated code changes:

- authentication (sign-in, tokens, sessions);
- authorization or ownership checks (who can read or change what);
- secrets or environment configuration;
- database permissions (grants, row level security, migrations that change them);
- how untrusted input is used: SQL, links the server will open, or text sent to an AI model.

The report:

- lists the potential security risks and edge cases;
- includes a unit or integration test for each one.

## 8. Quality bar

AI-generated code has the same bar as everything else: it follows the coding standards, passes CI, and is approved by a reviewer before merge.
