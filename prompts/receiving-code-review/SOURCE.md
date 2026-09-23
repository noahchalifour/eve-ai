# Where this comes from

`SKILL.md` beside this file is vendored verbatim from
[`obra/superpowers`](https://github.com/obra/superpowers), which is MIT
licensed (Copyright (c) 2025 Jesse Vincent).

| File here | Path upstream |
|---|---|
| `SKILL.md` | `skills/receiving-code-review/SKILL.md` |

**Pinned at upstream commit `3fb75974186ea7fada621d8ab77b3b02169baf57`.**

It was found with the `find-skills` skill (`npx skills find`), as EVE-31
asked, and is the `receiving-code-review` skill that issue names.

## Why vendored rather than installed

The same reason `prompts/code-review/SOURCE.md` gives for the review rubric.
This file is how Eve decides whether to act on a reviewer's comment, push
back, or ask, on pull requests she opened. If it could change without a
commit here, two follow-ups to the same review could differ for reasons absent
from this repository's history. Updating it is a visible pull request.

## How it is used

`eve_computer.acp.session.address_hint` points an `address` session's agent at
this file (`/app/prompts/receiving-code-review/SKILL.md` in the
`eve-computer` image, or the same relative path in an `eve-ai` checkout) and
tells it to follow it before changing anything. The skill is written for a
coding agent talking to its human partner; in an address session the
reviewer is whoever left the feedback, and "your human partner" is the family
member who asked for the change, so the hint states that mapping explicitly.

## Updating

Replace `SKILL.md` with the upstream file at a newer commit, update the
commit hash above, and open a pull request.
