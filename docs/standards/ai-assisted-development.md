# AI-Assisted Development

How AI-generated code is prompted, reviewed, tested and approved before merge.

AI-generated code is team-authored code. It follows [Coding Standards](coding-standards.md) and [Engineering Philosophy](philosophy.md).

## Tools

- Claude Code
- Codex

## Context

Give the tools everything except live data and environment variables.

## Review

No one merges AI-generated code they can't explain, verify and maintain.

## Dependencies and APIs

Verify AI-suggested dependencies and APIs against official documentation and confirm they work. New dependencies follow [Coding Standards §7](coding-standards.md#7-dependencies).

## Testing

Follows [Coding Standards §8](coding-standards.md#8-testing).

## Security review

Changes to authentication, authorization, secrets or database permissions need a security report in the PR. The report:

- Lists potential security risks and edge cases.
- Includes a unit or integration test for each one.

## Transcripts

AI session transcripts are not saved to the project.
