## User (2026-10-23T00:20:07)

why is this happening

<pasted_content id="a05b">
<user>@mac review-lens-ai % git switch -c branch docs/S1-001-coding-s
tandards
fatal: invalid reference: docs/S1-001-coding-standards
<user>@mac review-lens-ai %
</pasted_content id="a05b">

 
dont do anything just tell me what wrong and how to fix it

## Assistant (2026-10-23T00:20:12)

The extra word `branch` in your command is causing this.

`git switch -c` reads the **next** argument as the new branch name, and anything after that as the **starting point**, meaning the commit or branch to create it from. So git read your command as:

- create a new branch named **`branch`**
- starting from **`docs/S1-001-coding-standards`**

No branch or commit called `docs/S1-001-coding-standards` exists yet, so git fails with "invalid reference."

**Fix:** remove the word `branch`:

```bash
git switch -c docs/S1-001-coding-standards
```

That creates `docs/S1-001-coding-standards` from your current branch (`main`) and switches to it.
