# Engineering Philosophy

Working code is the minimum. The goal is code others can understand, test and change.

Before opening a PR, ask: **is my code easy to change?**

## 1. Simple

- Clear, descriptive names. Conventions are in [Coding Standards](coding-standards.md).
- Readable over clever.
- No unnecessary complexity, abstractions or dependencies.

## 2. Maintainable

- Another developer can understand it without your help.
- Document decisions, assumptions and non-obvious logic.
- Each component has one clear responsibility.
- Changes don't ripple into unrelated parts of the system.

## 3. DRY

- Keep each piece of knowledge in one authoritative place. Duplicates mean one fix becomes many.
- Reuse existing functions and components.
- Don't abstract code just because it looks similar.

## 4. Tested

- Unit test important logic and expected behavior.
- Cover edge cases and failures, not just the happy path.
- Tests are independent and repeatable.
- Bug fixes get a regression test when practical.

## Further reading

[Google: What to Look for in a Code Review](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
