# Where these files come from

The three files beside this one are vendored verbatim from
[`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills),
which is MIT licensed (Copyright (c) 2025 Addy Osmani).

| File here | Path upstream |
|---|---|
| `SKILL.md` | `skills/code-review-and-quality/SKILL.md` |
| `security-checklist.md` | `references/security-checklist.md` |
| `performance-checklist.md` | `references/performance-checklist.md` |

**Pinned at upstream commit `dc27a9c2e13721158157632de61b4106c6c2a2a1`.**

## Why these are vendored rather than installed

The rubric is the specification a review is performed against. If it can
change without a commit here, two reviews of the same pull request can differ
for reasons absent from this repository's history, and a model regression
becomes indistinguishable from an upstream rubric edit.

Installing at image build time (`npx skills add`) would track upstream
automatically, which is the wrong trade for a document whose stability is the
point. Updating the rubric is a visible pull request, which is also the only
honest way to review a change to how Eve reviews.

See `docs/superpowers/specs/2026-09-22-auto-review-prs-design.md`, section
"The rubric".

## Updating

Re-copy the three files from a newer upstream commit, update the pin above,
and open a pull request. Read the diff: it changes what Eve flags on every
future review.

## Local modifications

None. The files are verbatim. Stack scoping (skipping the frontend
performance sections when reviewing a Python service) is instructed in the
session prompt rather than by editing these files, so they stay diffable
against upstream.
