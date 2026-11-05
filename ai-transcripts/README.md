# AI transcripts

Transcripts of the AI-assisted development sessions for ReviewLens, kept here as the project requires (PRD §9.7). Dead ends and failed approaches are left in.

## What is here

| File | Session |
|---|---|
| `2026-10-23-session-1-understand-repo-and-plan.md` | First session: understanding the repo, the stories and the design |
| `2026-10-25-session-2-sprint-1-build-and-review.md` | Sprint 1 work: auth, data model, targets, tests, reviews of teammates' PRs |
| `2026-10-23-andy-session-1-git-branch-question.md` | A git branch error (short) |
| `2026-10-23-andy-session-2-sprint-1-env-auth-ownership-reviews.md` | Sprint 1 work: env and secrets (S1-008), route protection (S1-010), ownership (S1-011 to S1-013), the S1-022 schema sync and fix, demo preparation |

The first two sessions are by the developer who owns S1-009 and S1-014 to S1-018; the `andy` sessions are by the developer who owns S1-008 and S1-010 to S1-013. Both worked with Claude Code. Other team members add their own sessions here, the same way.

## What was changed before they were committed

Each file was exported from the tool's saved session and then scrubbed. The conversation itself is not edited.

- **Removed secrets:** every value from the project's `.env` files, database connection strings, JWTs, Supabase keys, GitHub and API tokens, bearer tokens, key and password assignments, the Supabase project id, and personal email addresses. Each is replaced by a marker such as `[REDACTED]`.
- **Shortened:** a command's output longer than 3,000 characters is cut, with a note saying how much was left out.
- **Left out:** the assistant's internal reasoning blocks, any images (screenshots), and the tool's automatic `<system-reminder>` notes.
- **Local paths:** the username in file paths is replaced by `<user>` (and the machine name by `<host>`).

Test values that appear in tests (for example `user:pass@...` in a link that must be rejected) are fake and were left as they are.

## Adding a transcript

1. Export the session from your tool. Claude Code saves each session as a JSON-lines file under `~/.claude/projects/`.
2. Convert it to readable text and remove everything listed above. Check the result by searching it for `postgres`, `eyJ`, `sb_`, `api_key`, `password`, `@` and your own `.env` values.
3. Add it here as `YYYY-MM-DD-<short-name>.md` and mention it in the PR.

Never commit a raw session file: it can contain secrets and personal data.
