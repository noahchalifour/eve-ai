# Auto-review pull requests implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eve reviews pull requests on GitHub, including her own, using a reviewer model deliberately different from the one that implemented the change, and posts findings as a non-blocking `COMMENT` review.

**Architecture:** A GitHub webhook reaches `eve-ambient`, which emits a `Signal` carrying a PR reference. Eve dispatches an ACP session on `eve-computer` that checks out the PR head detached, applies a vendored rubric, and writes `review.json`. The box validates that file and posts one review via `gh api`. The existing coding supervisor drives the session; the existing ambient pipeline carries the result back to the member.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 with `dict_row`, Alembic, pytest with `asyncio_mode = "auto"`, `agent-client-protocol` (the `computer` dependency group), `gh` CLI on the box.

**Spec:** `docs/superpowers/specs/2026-09-22-auto-review-prs-design.md`

## Global Constraints

- **Every new setting defaults to off or empty.** `review_enabled: bool = False`. A deployment that has not deliberately enabled this posts nothing.
- **The review verb is the hardcoded literal `"COMMENT"`.** No setting, no parameter, no branch may produce `APPROVE` or `REQUEST_CHANGES`.
- **`repo` and `pr_number` for any `gh` call come from the webhook payload only**, never from `review.json`.
- **Findings reach `gh` as a JSON file body**, never interpolated into a shell string.
- **Test tiers:** the default `pytest` run excludes `integration`, `live`, and `docker` markers. Every test in this plan is a unit test unless a step says otherwise.
- **`from __future__ import annotations`** at the top of every new Python module, matching every existing module in this repository.
- **Every SQL statement against a member-scoped table carries `member_sub`**, except the household-wide due query, which is named as a deliberate exception where it appears.
- **No em dashes in any prose or docstring.** Use commas, semicolons, colons, periods, or parentheses.
- **Commit after every task.** Conventional commit prefixes: `feat(review):`, `test(review):`, `docs(review):`, `chore(review):`.

## File structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/eve/review/__init__.py` | Empty package marker |
| `src/eve/review/dispatch.py` | Starts a review session: picks the reviewer pair, calls the box, writes the row |
| `src/eve/review/reviewer.py` | Pure function choosing agent and model given the implementing pair |
| `src/eve_ambient/sources/github.py` | Turns a verified webhook payload into a `Signal` |
| `src/eve_ambient/sources/review.py` | Polls resolved review sessions, emits result `Signal`s |
| `src/eve_computer/acp/review.py` | Reads and validates `review.json`, builds the GitHub review body |
| `alembic/versions/<n>_eve_coding_session_review.py` | Adds `kind`, `pr_number`, `head_sha` |
| `prompts/code-review/` | Vendored rubric (already committed, see `SOURCE.md`) |

**Modified:**

| Path | Change |
|---|---|
| `src/eve/settings.py` | Review settings block |
| `src/eve/family.py` | `Member.github_login`, and a `by_github_login` lookup |
| `family.yaml` | `github_login` and `code.review` for Noah |
| `src/eve/coding/store.py` | `kind`, `pr_number`, `head_sha` on create and read |
| `src/eve/coding/supervisor.py` | Review-aware decision prompt and close path |
| `src/eve/tools_client.py` | `create_review_session`, `close_review_session` |
| `src/eve_ambient/gates.py` | `"review": "code.review"` in `SOURCE_PERMISSION` |
| `src/eve_ambient/pipeline.py` | `"review"` joins `_REQUESTED_SOURCES` |
| `src/eve_ambient/sources/__init__.py` | Register the `review` source |
| `src/eve_ambient/app.py` | `POST /signals/github` |
| `src/eve_computer/acp/repo.py` | `add_review_worktree`, `post_review` |
| `src/eve_computer/acp/session.py` | `system_hint` parameter, review session creation |
| `src/eve_computer/app.py` | `POST /sessions/{id}/review` |
| `src/eve_computer/settings.py` | Review session bounds |
| `Dockerfile.eve-computer` | `COPY prompts/code-review` |

**Task order rationale:** Tasks 1 to 4 are leaves with no dependencies on each other and can be done in any order. Tasks 5 and 6 build the box's review capability. Task 7 wires Eve's dispatch. Tasks 8 and 9 build the trigger. Task 10 carries the result back. Task 11 is the end-to-end integration test.

---

### Task 1: Settings and family roster

**Files:**
- Modify: `src/eve/settings.py`
- Modify: `src/eve/family.py:23-35` (the `Member` dataclass) and `:45-58` (`from_yaml`)
- Modify: `src/eve_computer/settings.py`
- Modify: `family.yaml`
- Test: `tests/test_review_settings.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Settings.review_enabled: bool`, `review_webhook_secret: str`, `review_repos: list[str]`, `review_default_agent: str`, `review_default_model: str`
  - `ComputerSettings.review_session_timeout_seconds: int`, `max_concurrent_reviews: int`
  - `Member.github_login: str | None`
  - `Family.by_github_login(login: str) -> Member` raising `UnknownMemberError`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_settings.py`:

```python
"""Settings and roster additions for EVE-27."""

from __future__ import annotations

import pytest

from eve.family import Family, UnknownMemberError


def test_review_is_disabled_by_default():
    from eve.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.review_enabled is False
    assert settings.review_webhook_secret == ""
    assert settings.review_repos == []


def test_review_session_bounds_are_tighter_than_coding_bounds():
    """A review holding a session slot for four hours has failed at
    something other than reviewing."""
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    settings = get_computer_settings()

    assert settings.review_session_timeout_seconds < settings.session_timeout_seconds
    assert settings.max_concurrent_reviews >= 1


def test_a_member_can_carry_a_github_login(tmp_path):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: ['code.review']\n"
    )

    family = Family.from_yaml(roster)

    assert family.by_github_login("chalifournoah").sub == "sub-noah"


def test_an_unknown_github_login_is_rejected_rather_than_guessed(tmp_path):
    """A valid webhook signature proves GitHub sent the event, not that the
    labeller may spend Eve's tokens."""
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: []\n"
    )

    family = Family.from_yaml(roster)

    with pytest.raises(UnknownMemberError):
        family.by_github_login("a-stranger")


def test_a_member_without_a_github_login_never_matches(tmp_path):
    """`None` must not collide with a missing login on the lookup side."""
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-kid'\n"
        "    name: 'Kid'\n"
        "    role: child\n"
        "    timezone: 'America/Vancouver'\n"
        "    permissions: []\n"
    )

    family = Family.from_yaml(roster)

    with pytest.raises(UnknownMemberError):
        family.by_github_login("")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_review_settings.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'review_enabled'` and `AttributeError: 'Family' object has no attribute 'by_github_login'`.

- [ ] **Step 3: Add the settings**

In `src/eve/settings.py`, after the `coding_*` block:

```python
    # EVE-27 (auto-review pull requests). See docs/superpowers/specs/
    # 2026-09-22-auto-review-prs-design.md.
    #
    # Off by default for the same reason coding_enabled is, and for one more:
    # this is the first path where the public internet reaches a cluster
    # service on a schedule Eve does not control. A deployment that has not
    # deliberately enabled outbound review comments under Eve's GitHub
    # identity posts none.
    review_enabled: bool = False
    # GitHub's webhook secret. The signature is an HMAC over the raw body,
    # not a shared secret compared directly, so this is the HMAC key.
    review_webhook_secret: str = ""
    # The allowlist. A webhook can name any repository; only these spend
    # tokens. Empty means none, which is the safe reading of "not configured".
    review_repos: list[str] = Field(default_factory=list)
    # The reviewer for a human-authored pull request, which has no
    # implementing pair to differ from. Deliberately a strong model: this is
    # the case where nothing else constrains the choice.
    review_default_agent: str = "claude"
    review_default_model: str = "anthropic/claude-sonnet-5"
```

In `src/eve_computer/settings.py`, after the session bounds:

```python
    # EVE-27. A review is a session with a tighter ceiling than a coding
    # session: four hours of reviewing is a failure at something other than
    # reviewing, and it holds a slot a human is waiting on.
    review_session_timeout_seconds: int = 3600
    # Separate from max_concurrent_sessions so a burst of labelled pull
    # requests cannot starve the delegated coding work that
    # `check_coding_session` promises a member is in flight.
    max_concurrent_reviews: int = 2
```

- [ ] **Step 4: Add `github_login` to the roster**

In `src/eve/family.py`, add to the `Member` dataclass after `wardrobe_album`:

```python
    # EVE-27: the GitHub account whose label or assignment may commission a
    # review as this member. Optional, and a member without one can never be
    # resolved from a webhook, which is the safe direction.
    github_login: str | None = None
```

In `from_yaml`, inside the `Member(...)` construction:

```python
                    github_login=entry.get("github_login") or None,
```

Add to the `Family` class, after `get`:

```python
    def by_github_login(self, login: str) -> Member:
        """The member a GitHub webhook's actor maps to.

        A valid webhook signature proves GitHub sent the event; it does not
        prove the actor may spend Eve's tokens. This is where that second
        question is answered, and it fails closed: an empty or unknown login
        raises rather than defaulting to anybody.
        """
        if not login:
            raise UnknownMemberError("no family member with an empty GitHub login")
        for member in self._by_sub.values():
            if member.github_login == login:
                return member
        raise UnknownMemberError(f"no family member with GitHub login {login!r}")
```

- [ ] **Step 5: Grant the permission in the roster**

In `family.yaml`, in Noah's entry, add `github_login` beside `timezone`:

```yaml
    # EVE-27: the GitHub account that may commission a review by labelling
    # or assigning a pull request.
    github_login: "chalifournoah"
```

and add to his `permissions` list:

```yaml
      # EVE-27: commissioning a review by labelling a pull request. Distinct
      # from code.delegate: delegating writes code, this only comments on it.
      - code.review
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_review_settings.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 7: Run the full unit suite for regressions**

Run: `uv run pytest -q`
Expected: PASS. The `Member` dataclass gained a defaulted field, so existing construction sites are unaffected.

- [ ] **Step 8: Commit**

```bash
git add src/eve/settings.py src/eve/family.py src/eve_computer/settings.py family.yaml tests/test_review_settings.py
git commit -m "feat(review): settings, roster github_login, and the code.review grant"
```

---

### Task 2: Choosing the reviewer

**Files:**
- Create: `src/eve/review/__init__.py`
- Create: `src/eve/review/reviewer.py`
- Test: `tests/test_review_reviewer.py`

**Interfaces:**
- Consumes: `Settings.review_default_agent`, `Settings.review_default_model` (Task 1)
- Produces: `choose(implemented_by: tuple[str, str] | None) -> tuple[str, str]` returning `(agent, model)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_reviewer.py`:

```python
"""The issue's central requirement: a reviewer that is not the implementer."""

from __future__ import annotations

import pytest

from eve.review import reviewer


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_AGENT", "claude")
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_MODEL", "anthropic/claude-sonnet-5")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_human_pull_request_gets_the_configured_default():
    assert reviewer.choose(None) == ("claude", "anthropic/claude-sonnet-5")


def test_an_eve_pull_request_is_reviewed_by_a_different_agent():
    agent, model = reviewer.choose(("dsh", "chatgpt/gpt-5.6-sol"))

    assert agent != "dsh"
    assert model != "chatgpt/gpt-5.6-sol"


def test_the_default_is_avoided_when_it_is_what_implemented_the_change():
    """The whole point is a different blind spot. When the configured
    default is the implementer, it must not be chosen anyway."""
    agent, model = reviewer.choose(("claude", "anthropic/claude-sonnet-5"))

    assert agent != "claude"
    assert model != "anthropic/claude-sonnet-5"


def test_the_choice_is_a_known_agent():
    from eve.coding.dispatch import AGENTS

    for implemented_by in (None, ("dsh", "m"), ("claude", "anthropic/claude-sonnet-5")):
        agent, _model = reviewer.choose(implemented_by)
        assert agent in AGENTS


def test_the_choice_is_stable_for_the_same_input():
    """Two reviews of the same pull request must not differ because the
    reviewer was picked at random."""
    first = reviewer.choose(("dsh", "chatgpt/gpt-5.6-sol"))
    second = reviewer.choose(("dsh", "chatgpt/gpt-5.6-sol"))

    assert first == second
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_review_reviewer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.review'`.

- [ ] **Step 3: Create the package and the chooser**

Create `src/eve/review/__init__.py` as an empty file.

Create `src/eve/review/reviewer.py`:

```python
"""Which agent and model review a pull request.

THE ONE RULE IN THIS FILE. The reviewer is never the implementer. EVE-27
asks for a different provider or model than the one that wrote the code, and
the reason is blind spots: a model reviewing its own output shares every
assumption that produced the bug. Falling back to the implementer when the
configured default happens to match it would lose exactly the property the
feature exists for.

Pure and deterministic, with no I/O and no model call. Two reviews of the
same pull request must not differ because the reviewer was drawn at random,
or the member cannot tell a model regression from a coin flip.
"""

from __future__ import annotations

from eve.settings import get_settings

# Ordered by how much this repository trusts them to review rather than
# write, which is not the same ranking as the coding fallback: a reviewer is
# read-only, so a strong reasoner with weak tool use is fine here and would
# be a poor coding agent.
_PREFERENCE: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic/claude-sonnet-5"),
    ("codex", "chatgpt/gpt-5.6-sol"),
    ("dsh", "chatgpt/gpt-5.6-sol"),
)


def choose(implemented_by: tuple[str, str] | None) -> tuple[str, str]:
    """`(agent, model)` for the review.

    `implemented_by` is the pair recorded on the coding session that opened
    the pull request, or `None` for a human-authored one, where there is
    nothing to differ from.
    """
    settings = get_settings()
    default = (settings.review_default_agent, settings.review_default_model)

    if implemented_by is None:
        return default

    implementer_agent, implementer_model = implemented_by
    candidates = (default, *_PREFERENCE)
    for agent, model in candidates:
        if agent != implementer_agent and model != implementer_model:
            return agent, model

    # Every candidate collides, which means the preference table has been
    # narrowed to one entry. Returning the implementer would silently drop
    # the feature's whole premise, so take the first candidate differing on
    # agent alone and accept the weaker guarantee.
    for agent, model in candidates:
        if agent != implementer_agent:
            return agent, model
    return default
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_review_reviewer.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/review tests/test_review_reviewer.py
git commit -m "feat(review): choose a reviewer that is not the implementer"
```

---

### Task 3: The session row gains a kind

**Files:**
- Create: `alembic/versions/<next>_eve_coding_session_review.py`
- Modify: `src/eve/coding/store.py:22-39` (`create_session`)
- Test: `tests/test_review_store.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `store.create_session(..., kind: str = "code", pr_number: int | None = None, head_sha: str | None = None)`
  - `store.review_exists_for(repo: str, pr_number: int, head_sha: str) -> bool`

**Note on the migration number:** run `uv run alembic heads` first and set `down_revision` to whatever it reports. The routines design (EVE-25) also claims `0011`, so do not assume it is free.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_store.py`:

```python
"""The review columns on eve_coding_session.

Marked `integration`: these run against the real Postgres from
docker-compose.test.yml, like every other store test in this repository.
"""

from __future__ import annotations

import uuid

import pytest

from eve.coding import store

pytestmark = pytest.mark.integration


async def test_a_coding_session_defaults_to_kind_code():
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id="t1",
        goal="fix the thing", agent="dsh", model="m", repos=["acme/repo"],
        context="",
    )

    row = await store.get(session_id)

    assert row["kind"] == "code"
    assert row["pr_number"] is None
    assert row["head_sha"] is None


async def test_a_review_session_records_the_pull_request_it_reviews():
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id="t1",
        goal="review acme/repo#7", agent="claude", model="m",
        repos=["acme/repo"], context="", kind="review", pr_number=7,
        head_sha="abc123",
    )

    row = await store.get(session_id)

    assert row["kind"] == "review"
    assert row["pr_number"] == 7
    assert row["head_sha"] == "abc123"


async def test_an_already_reviewed_commit_is_recognised():
    """The idempotence key. A relabelled pull request whose code has not
    changed must not buy a second review."""
    session_id = str(uuid.uuid4())
    sha = uuid.uuid4().hex
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id="t1",
        goal="review", agent="claude", model="m", repos=["acme/repo"],
        context="", kind="review", pr_number=7, head_sha=sha,
    )

    assert await store.review_exists_for("acme/repo", 7, sha) is True
    assert await store.review_exists_for("acme/repo", 7, "a-different-sha") is False
    assert await store.review_exists_for("acme/other", 7, sha) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_review_store.py -m integration -v`
Expected: FAIL with a psycopg `UndefinedColumn` error for `kind`.

If the integration services are not running, start them first:
`docker compose -f docker-compose.test.yml up -d`

- [ ] **Step 3: Find the current migration head**

Run: `uv run alembic heads`
Note the revision id it prints. That is this migration's `down_revision`.

- [ ] **Step 4: Write the migration**

Create `alembic/versions/<next>_eve_coding_session_review.py`, replacing `<HEAD>` with what Step 3 printed:

```python
"""Review columns on eve_coding_session (EVE-27).

A review is a coding session with a different beginning and a different
ending, so it is a `kind` on the existing row rather than a second table:
every query, the supervisor loop, the stale timeout, and the ambient source
are identical for both, and a parallel table would duplicate all of it to
express one enum.
"""

from alembic import op
import sqlalchemy as sa

revision = "<next>_eve_coding_session_review"
down_revision = "<HEAD>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eve_coding_session",
        sa.Column("kind", sa.Text(), nullable=False, server_default="code"),
    )
    op.add_column(
        "eve_coding_session", sa.Column("pr_number", sa.Integer(), nullable=True)
    )
    op.add_column(
        "eve_coding_session", sa.Column("head_sha", sa.Text(), nullable=True)
    )
    # The idempotence lookup: "has this exact commit already been reviewed".
    # Partial, because it answers a question only review rows can be asked.
    op.create_index(
        "eve_coding_session_review_commit",
        "eve_coding_session",
        ["pr_number", "head_sha"],
        postgresql_where=sa.text("kind = 'review'"),
    )


def downgrade() -> None:
    op.drop_index("eve_coding_session_review_commit", table_name="eve_coding_session")
    op.drop_column("eve_coding_session", "head_sha")
    op.drop_column("eve_coding_session", "pr_number")
    op.drop_column("eve_coding_session", "kind")
```

- [ ] **Step 5: Extend the store**

In `src/eve/coding/store.py`, replace `create_session` with:

```python
async def create_session(
    session_id: str,
    member_sub: str,
    thread_id: str,
    goal: str,
    agent: str,
    model: str,
    repos: list[str],
    context: str,
    kind: str = "code",
    pr_number: int | None = None,
    head_sha: str | None = None,
) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_coding_session"
            " (id, member_sub, thread_id, goal, agent, model, repos, context,"
            "  status, kind, pr_number, head_sha)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s)",
            (
                session_id, member_sub, thread_id, goal, agent, model,
                Jsonb(repos), context, kind, pr_number, head_sha,
            ),
        )
```

Add at the end of the file:

```python
async def review_exists_for(repo: str, pr_number: int, head_sha: str) -> bool:
    """Whether this exact commit on this pull request has already been
    reviewed.

    Deliberately not scoped to a member: the question is about a commit, and
    a second member relabelling a pull request Eve already reviewed should
    get the existing review rather than a duplicate one. That makes this the
    second of the two household-wide reads in this module, alongside
    `live_sessions`.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT 1 FROM eve_coding_session"
                " WHERE kind = 'review' AND pr_number = %s AND head_sha = %s"
                "   AND repos ? %s"
                " LIMIT 1",
                (pr_number, head_sha, repo),
            )
            return await cur.fetchone() is not None
```

- [ ] **Step 6: Apply the migration and run the tests**

Run: `uv run alembic upgrade head && uv run pytest tests/test_review_store.py -m integration -v`
Expected: PASS, 3 tests.

- [ ] **Step 7: Verify the migration graph test still passes**

Run: `uv run pytest tests/test_alembic_graph.py -v`
Expected: PASS. This repository has a test asserting a single migration head; a second head here would be caught now rather than on deploy.

- [ ] **Step 8: Commit**

```bash
git add alembic/versions src/eve/coding/store.py tests/test_review_store.py
git commit -m "feat(review): kind, pr_number, and head_sha on the session row"
```

---

### Task 4: The PR checkout

**Files:**
- Modify: `src/eve_computer/acp/repo.py` (add after `add_worktree`)
- Test: `tests/test_acp_repo_review.py`

**Interfaces:**
- Consumes: `repo.ensure_clone`, `repo._qualified`, `repo._run` (existing)
- Produces: `add_review_worktree(repo: str, session_dir: Path, pr_number: int, base_ref: str) -> dict` returning `{"path": Path, "merge_base": str, "head_sha": str}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_repo_review.py`:

```python
"""Real git against a real temporary origin, mirroring test_acp_repo.py.

Mocking git here would test the mock: `refs/pull/<n>/head`, detached
worktrees, and merge-base computation are exactly the parts that would break
in production while a mocked test stayed green.
"""

from __future__ import annotations

import subprocess

import pytest

from eve_computer.acp import repo as repo_mod
from eve_computer.acp.repo import add_review_worktree


def _run(*args, cwd):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path):
    """A bare origin with `main`, plus a PR branch published at
    `refs/pull/7/head` the way GitHub publishes one."""
    bare = tmp_path / "origin" / "acme.git"
    bare.mkdir(parents=True)
    _run("git", "init", "--bare", "--initial-branch=main", ".", cwd=bare)

    seed = tmp_path / "seed"
    seed.mkdir()
    _run("git", "init", "--initial-branch=main", ".", cwd=seed)
    _run("git", "config", "user.email", "eve@example.com", cwd=seed)
    _run("git", "config", "user.name", "Eve", cwd=seed)
    (seed / "README.md").write_text("hello\n")
    _run("git", "add", "README.md", cwd=seed)
    _run("git", "commit", "-m", "seed", cwd=seed)
    _run("git", "remote", "add", "origin", str(bare), cwd=seed)
    _run("git", "push", "-u", "origin", "main", cwd=seed)

    # The pull request: one commit off main, published where GitHub puts it.
    _run("git", "checkout", "-b", "feature", cwd=seed)
    (seed / "feature.py").write_text("def f():\n    return 1\n")
    _run("git", "add", "feature.py", cwd=seed)
    _run("git", "commit", "-m", "add feature", cwd=seed)
    _run("git", "push", "origin", "feature:refs/pull/7/head", cwd=seed)

    # main moves on afterwards, so a two-dot diff would show this commit as
    # part of the pull request and a three-dot diff would not.
    _run("git", "checkout", "main", cwd=seed)
    (seed / "unrelated.py").write_text("x = 1\n")
    _run("git", "add", "unrelated.py", cwd=seed)
    _run("git", "commit", "-m", "unrelated work on main", cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)
    return bare


@pytest.fixture(autouse=True)
def _settings(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_GITHUB_OWNER", "acme")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    monkeypatch.setattr(repo_mod, "_clone_url", lambda name: str(origin))
    yield
    get_computer_settings.cache_clear()


async def test_the_worktree_holds_the_pull_requests_code(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    assert (result["path"] / "feature.py").exists()


async def test_the_checkout_is_detached_so_no_branch_is_created(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    head = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=result["path"], capture_output=True, text=True,
    )
    assert head.returncode != 0, "HEAD should be detached, not on a branch"


async def test_the_merge_base_excludes_work_main_gained_afterwards(tmp_path):
    """Three-dot semantics. A pull request opened against a branch that has
    since moved must not show that branch's later commits as findings."""
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    files = subprocess.run(
        ["git", "diff", "--name-only", f"{result['merge_base']}...HEAD"],
        cwd=result["path"], capture_output=True, text=True, check=True,
    ).stdout.split()
    assert files == ["feature.py"]
    assert "unrelated.py" not in files


async def test_the_head_sha_identifies_the_reviewed_commit(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=result["path"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert result["head_sha"] == actual
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_acp_repo_review.py -v`
Expected: FAIL with `ImportError: cannot import name 'add_review_worktree'`.

- [ ] **Step 3: Implement it**

In `src/eve_computer/acp/repo.py`, add after `add_worktree`:

```python
async def add_review_worktree(
    repo: str, session_dir: Path, pr_number: int, base_ref: str
) -> dict:
    """A detached checkout of a pull request's head, plus the merge base to
    diff against.

    WHY DETACHED. A review creates no branch and pushes nothing, so there is
    no branch to name and none to leak. `remove_worktrees` then tears it down
    unchanged.

    WHY THE MERGE BASE IS RETURNED RATHER THAN THE BASE REF. The reviewer has
    to diff three-dot (`merge_base...HEAD`). A pull request opened against a
    branch that has since moved would otherwise show that branch's later
    commits as findings, and a reviewer reporting someone else's commits as
    problems in this change is worse than no reviewer.
    """
    clone = await ensure_clone(repo)
    head_ref = f"refs/pull/{pr_number}/head"
    # A named local ref, so the fetched commit survives a later `git gc` in
    # the clone while the worktree is still using it.
    local_ref = f"refs/eve-review/{pr_number}"
    await _run("git", "fetch", "origin", f"+{head_ref}:{local_ref}", cwd=clone)

    head_sha = await _run("git", "rev-parse", local_ref, cwd=clone)
    merge_base = await _run(
        "git", "merge-base", f"origin/{base_ref.split('/')[-1]}", head_sha, cwd=clone
    )

    tree = worktree_path(session_dir, repo)
    tree.parent.mkdir(parents=True, exist_ok=True)
    await _run("git", "worktree", "add", "--detach", str(tree), head_sha, cwd=clone)
    return {"path": tree, "merge_base": merge_base, "head_sha": head_sha}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_acp_repo_review.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Run the existing repo tests for regressions**

Run: `uv run pytest tests/test_acp_repo.py -v`
Expected: PASS. Nothing existing was modified, only added to.

- [ ] **Step 6: Commit**

```bash
git add src/eve_computer/acp/repo.py tests/test_acp_repo_review.py
git commit -m "feat(review): detached PR-head worktrees with a merge base"
```

---

### Task 5: Validating the findings file

**Files:**
- Create: `src/eve_computer/acp/review.py`
- Test: `tests/test_acp_review_findings.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `SEVERITIES: tuple[str, ...]`, `AXES: tuple[str, ...]`
  - `class InvalidFindings(Exception)`
  - `load(session_dir: Path) -> dict` raising `InvalidFindings`
  - `build_body(review: dict, agent: str, model: str) -> str`
  - `split_comments(review: dict, changed_lines: dict[str, set[int]]) -> tuple[list[dict], list[dict]]` returning `(anchorable, demoted)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_review_findings.py`:

```python
"""`review.json` is model output, so it is untrusted input to a `gh` call.

This is the security checklist's own LLM05 (Improper Output Handling)
applied to ourselves.
"""

from __future__ import annotations

import json

import pytest

from eve_computer.acp import review as review_mod
from eve_computer.acp.review import InvalidFindings, build_body, load, split_comments


def _write(tmp_path, payload):
    (tmp_path / "review.json").write_text(json.dumps(payload))
    return tmp_path


def _valid():
    return {
        "summary": "Two findings.",
        "skipped_sections": ["performance: frontend (Python service)"],
        "findings": [
            {
                "severity": "critical", "axis": "security",
                "file": "src/eve/a.py", "line": 10, "body": "no permission check",
            },
            {
                "severity": "nit", "axis": "readability",
                "file": "src/eve/a.py", "line": 20, "body": "unclear name",
            },
        ],
    }


def test_a_well_formed_findings_file_loads(tmp_path):
    review = load(_write(tmp_path, _valid()))

    assert len(review["findings"]) == 2
    assert review["skipped_sections"] == ["performance: frontend (Python service)"]


def test_a_missing_file_is_a_failure_not_an_empty_review(tmp_path):
    """A review that silently degrades into 'no findings' is worse than no
    review, because it looks like a review."""
    with pytest.raises(InvalidFindings, match="no review.json"):
        load(tmp_path)


def test_malformed_json_is_rejected(tmp_path):
    (tmp_path / "review.json").write_text("{not json")

    with pytest.raises(InvalidFindings):
        load(tmp_path)


def test_an_unknown_severity_is_rejected(tmp_path):
    payload = _valid()
    payload["findings"][0]["severity"] = "catastrophic"

    with pytest.raises(InvalidFindings, match="severity"):
        load(_write(tmp_path, payload))


def test_an_unknown_axis_is_rejected(tmp_path):
    payload = _valid()
    payload["findings"][0]["axis"] = "vibes"

    with pytest.raises(InvalidFindings, match="axis"):
        load(_write(tmp_path, payload))


def test_a_finding_without_a_file_is_rejected(tmp_path):
    payload = _valid()
    del payload["findings"][0]["file"]

    with pytest.raises(InvalidFindings, match="file"):
        load(_write(tmp_path, payload))


def test_findings_must_be_a_list(tmp_path):
    with pytest.raises(InvalidFindings):
        load(_write(tmp_path, {"summary": "x", "findings": "none"}))


def test_a_review_with_no_findings_is_valid(tmp_path):
    """Clean code is a legitimate outcome, not a malformed file."""
    review = load(_write(tmp_path, {"summary": "Looks good.", "findings": []}))

    assert review["findings"] == []


def test_the_body_names_the_reviewing_agent_and_model(tmp_path):
    """The member can only check that a different model reviewed than
    implemented if it is written where they look."""
    body = build_body(_valid(), "claude", "anthropic/claude-sonnet-5")

    assert "claude" in body
    assert "anthropic/claude-sonnet-5" in body


def test_the_body_reports_counts_and_skipped_sections():
    body = build_body(_valid(), "claude", "m")

    assert "critical" in body.lower()
    assert "frontend" in body


def test_an_unanchorable_finding_is_demoted_rather_than_dropped():
    """GitHub rejects the whole review if one comment is unanchorable, so one
    bad line number must not cost the other findings."""
    changed = {"src/eve/a.py": {10}}

    anchorable, demoted = split_comments(_valid(), changed)

    assert len(anchorable) == 1
    assert anchorable[0]["line"] == 10
    assert len(demoted) == 1
    assert demoted[0]["line"] == 20


def test_a_finding_in_an_untouched_file_is_demoted():
    anchorable, demoted = split_comments(_valid(), {"src/eve/other.py": {10, 20}})

    assert anchorable == []
    assert len(demoted) == 2


def test_demoted_findings_appear_in_the_body():
    changed = {"src/eve/a.py": {10}}
    _anchorable, demoted = split_comments(_valid(), changed)

    body = build_body(_valid(), "claude", "m", demoted=demoted)

    assert "unclear name" in body


def test_severity_prefixes_use_the_rubrics_vocabulary():
    changed = {"src/eve/a.py": {10, 20}}
    anchorable, _demoted = split_comments(_valid(), changed)

    critical = next(c for c in anchorable if c["line"] == 10)
    nit = next(c for c in anchorable if c["line"] == 20)
    assert critical["body"].startswith("**Critical:**")
    assert nit["body"].startswith("**Nit:**")


def test_a_required_finding_carries_no_prefix():
    """The rubric's table: no prefix means required."""
    payload = _valid()
    payload["findings"] = [{
        "severity": "required", "axis": "correctness",
        "file": "src/eve/a.py", "line": 10, "body": "off by one",
    }]

    anchorable, _demoted = split_comments(payload, {"src/eve/a.py": {10}})

    assert anchorable[0]["body"] == "off by one"


def test_every_declared_severity_is_renderable():
    """A severity that loads but cannot render would fail at post time,
    after the tokens were already spent."""
    for severity in review_mod.SEVERITIES:
        payload = {
            "summary": "x",
            "findings": [{
                "severity": severity, "axis": "correctness",
                "file": "a.py", "line": 1, "body": "text",
            }],
        }
        anchorable, _ = split_comments(payload, {"a.py": {1}})
        assert anchorable[0]["body"].endswith("text")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_acp_review_findings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_computer.acp.review'`.

- [ ] **Step 3: Implement the module**

Create `src/eve_computer/acp/review.py`:

```python
"""`review.json` in, a GitHub review body out.

WHY THIS VALIDATES RATHER THAN TRUSTS. The findings file is model output.
The security checklist this very feature reviews against says to treat model
output as untrusted and to validate it at the boundary (LLM05, Improper
Output Handling), and the boundary is here: everything downstream of this
module is an HTTP call to GitHub under Eve's identity.

WHY AN UNANCHORABLE FINDING IS DEMOTED RATHER THAN DROPPED. GitHub rejects
an entire review if any one inline comment names a line outside the diff. A
reviewer that miscounts one line number must not cost the other twenty-nine
findings, and silently dropping it would hide a real finding.
"""

from __future__ import annotations

import json
from pathlib import Path

SEVERITIES: tuple[str, ...] = ("critical", "required", "optional", "nit", "fyi")
AXES: tuple[str, ...] = (
    "correctness", "readability", "architecture", "security", "performance",
)

# The rubric's own table: no prefix means required, so the author can tell
# what is mandatory from what is taste.
_PREFIX: dict[str, str] = {
    "critical": "**Critical:** ",
    "required": "",
    "optional": "**Optional:** ",
    "nit": "**Nit:** ",
    "fyi": "**FYI:** ",
}

_FILE_NAME = "review.json"


class InvalidFindings(Exception):
    """`review.json` is absent, unparseable, or does not say what it must."""


def load(session_dir: Path) -> dict:
    path = Path(session_dir) / _FILE_NAME
    if not path.is_file():
        raise InvalidFindings(f"no {_FILE_NAME} in {session_dir}")
    try:
        review = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise InvalidFindings(f"{_FILE_NAME} could not be read: {exc}") from exc

    if not isinstance(review, dict):
        raise InvalidFindings(f"{_FILE_NAME} is not an object")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise InvalidFindings(f"{_FILE_NAME} has no findings list")

    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise InvalidFindings(f"finding {index} is not an object")
        if finding.get("severity") not in SEVERITIES:
            raise InvalidFindings(
                f"finding {index} has severity {finding.get('severity')!r};"
                f" expected one of {', '.join(SEVERITIES)}"
            )
        if finding.get("axis") not in AXES:
            raise InvalidFindings(
                f"finding {index} has axis {finding.get('axis')!r};"
                f" expected one of {', '.join(AXES)}"
            )
        if not finding.get("file"):
            raise InvalidFindings(f"finding {index} names no file")
        if not finding.get("body"):
            raise InvalidFindings(f"finding {index} has no body")

    review.setdefault("summary", "")
    review.setdefault("skipped_sections", [])
    return review


def _rendered(finding: dict) -> str:
    return f"{_PREFIX[finding['severity']]}{finding['body']}"


def split_comments(
    review: dict, changed_lines: dict[str, set[int]]
) -> tuple[list[dict], list[dict]]:
    """`(anchorable, demoted)`. Anchorable comments become inline review
    comments; demoted ones ride in the summary body."""
    anchorable: list[dict] = []
    demoted: list[dict] = []
    for finding in review["findings"]:
        line = finding.get("line")
        lines = changed_lines.get(finding["file"], set())
        if isinstance(line, int) and line in lines:
            anchorable.append({
                "path": finding["file"],
                "line": line,
                "side": "RIGHT",
                "body": _rendered(finding),
            })
        else:
            demoted.append({**finding, "line": line})
    return anchorable, demoted


def build_body(
    review: dict, agent: str, model: str, demoted: list[dict] | None = None
) -> str:
    counts: dict[str, int] = {}
    for finding in review["findings"]:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    tally = ", ".join(f"{n} {s}" for s, n in counts.items()) or "no findings"

    lines = [
        review.get("summary", ""),
        "",
        f"**Findings:** {tally}.",
    ]
    skipped = review.get("skipped_sections") or []
    if skipped:
        lines.append(f"**Rubric sections skipped:** {'; '.join(skipped)}.")
    if demoted:
        lines.append("")
        lines.append("**Findings that could not be anchored to a changed line:**")
        lines.extend(
            f"- `{f['file']}`"
            f"{f':{f[\"line\"]}' if isinstance(f.get('line'), int) else ''}: "
            f"{_rendered(f)}"
            for f in demoted
        )
    lines.extend([
        "",
        f"<sub>Reviewed by `{agent}` / `{model}`. This review is advisory and "
        f"carries no merge authority.</sub>",
    ])
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_acp_review_findings.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_computer/acp/review.py tests/test_acp_review_findings.py
git commit -m "feat(review): validate review.json and render the GitHub body"
```

---

### Task 6: Posting the review

**Files:**
- Modify: `src/eve_computer/acp/repo.py` (add after `publish`)
- Test: `tests/test_acp_repo_post_review.py`

**Interfaces:**
- Consumes: `review.build_body`, `review.split_comments` (Task 5)
- Produces:
  - `changed_lines(tree: Path, merge_base: str) -> dict[str, set[int]]`
  - `post_review(repo: str, pr_number: int, review: dict, agent: str, model: str, tree: Path, merge_base: str) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_repo_post_review.py`:

```python
"""Posting is the one place an LLM's output reaches Eve's GitHub identity.

`gh` is faked via a PATH script that records its argv, the same technique
test_acp_repo.py uses: there is no GitHub in a unit test, and the code's
contract with `gh` is an argv and a request body.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from eve_computer.acp.repo import post_review


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def worktree(tmp_path):
    """A real checkout with one changed file, so `changed_lines` has real
    diff output to parse."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _run("git", "init", "--initial-branch=main", ".", cwd=tree)
    _run("git", "config", "user.email", "eve@example.com", cwd=tree)
    _run("git", "config", "user.name", "Eve", cwd=tree)
    (tree / "a.py").write_text("one\ntwo\nthree\n")
    _run("git", "add", "a.py", cwd=tree)
    _run("git", "commit", "-m", "base", cwd=tree)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True,
    ).stdout.strip()
    (tree / "a.py").write_text("one\nCHANGED\nthree\n")
    _run("git", "add", "a.py", cwd=tree)
    _run("git", "commit", "-m", "change", cwd=tree)
    return tree, base


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A `gh` on PATH recording argv and any --input file it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "gh-argv.log"
    body_log = tmp_path / "gh-body.json"
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {argv_log}\n'
        "prev=\"\"\n"
        "for arg in \"$@\"; do\n"
        f'  if [ "$prev" = "--input" ]; then cp "$arg" {body_log}; fi\n'
        "  prev=\"$arg\"\n"
        "done\n"
        'case "$*" in\n'
        f'  *"--paginate"*) echo "[]" ;;\n'
        '  *) echo "{}" ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return argv_log, body_log


def _review():
    return {
        "summary": "One finding.",
        "skipped_sections": [],
        "findings": [{
            "severity": "critical", "axis": "security",
            "file": "a.py", "line": 2, "body": "unsafe",
        }],
    }


async def test_the_review_is_posted_as_a_comment(fake_gh, worktree):
    argv_log, body_log = fake_gh
    tree, base = worktree

    await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    body = json.loads(body_log.read_text())
    assert body["event"] == "COMMENT"


async def test_approve_is_not_reachable(fake_gh, worktree):
    """Hardcoded, not configured: no input may produce merge authority."""
    argv_log, body_log = fake_gh
    tree, base = worktree

    await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    posted = body_log.read_text()
    assert "APPROVE" not in posted
    assert "REQUEST_CHANGES" not in posted


async def test_the_repo_and_pr_come_from_the_caller_not_the_findings(
    fake_gh, worktree
):
    """The agent must not be able to redirect a review onto another repo."""
    argv_log, body_log = fake_gh
    tree, base = worktree
    hostile = _review()
    hostile["repo"] = "someone-else/private"
    hostile["pr_number"] = 999

    await post_review("acme/repo", 7, hostile, "claude", "m", tree, base)

    argv = argv_log.read_text()
    assert "acme/repo/pulls/7/reviews" in argv
    assert "someone-else" not in argv
    assert "999" not in argv


async def test_finding_text_cannot_become_a_shell_argument(fake_gh, worktree):
    """Findings reach gh as a JSON file body, never interpolated into a
    shell string."""
    argv_log, body_log = fake_gh
    tree, base = worktree
    hostile = _review()
    hostile["findings"][0]["body"] = "; rm -rf / #"

    await post_review("acme/repo", 7, hostile, "claude", "m", tree, base)

    assert "rm -rf" not in argv_log.read_text()
    assert "rm -rf" in body_log.read_text()


async def test_one_api_call_rather_than_one_per_finding(fake_gh, worktree):
    argv_log, body_log = fake_gh
    tree, base = worktree
    many = _review()
    many["findings"] = [
        {"severity": "nit", "axis": "readability", "file": "a.py",
         "line": 2, "body": f"finding {i}"}
        for i in range(10)
    ]

    await post_review("acme/repo", 7, many, "claude", "m", tree, base)

    posts = [l for l in argv_log.read_text().splitlines() if "reviews" in l and "--paginate" not in l]
    assert len(posts) == 1


async def test_a_gh_failure_is_reported_rather_than_raised(
    tmp_path, monkeypatch, worktree
):
    """The review took real time and real tokens; losing the findings
    because the post failed would waste all of it."""
    tree, base = worktree
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text("#!/bin/sh\necho 'boom' >&2\nexit 1\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    assert result["posted"] is False
    assert "error" in result


async def test_an_existing_review_for_the_same_commit_is_not_duplicated(
    tmp_path, monkeypatch, worktree
):
    tree, base = worktree
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "argv.log"
    script = bin_dir / "gh"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True,
    ).stdout.strip()
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {argv_log}\n'
        'case "$*" in\n'
        f'  *"--paginate"*) echo \'[{{"commit_id": "{head}", "user": {{"login": "eve"}}}}]\' ;;\n'
        '  *) echo "{}" ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    assert result["skipped"] is True
    posts = [l for l in argv_log.read_text().splitlines() if "--input" in l]
    assert posts == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_acp_repo_post_review.py -v`
Expected: FAIL with `ImportError: cannot import name 'post_review'`.

- [ ] **Step 3: Implement it**

In `src/eve_computer/acp/repo.py`, add these imports at the top:

```python
import json
import tempfile

from eve_computer.acp import review as review_findings
```

and add after `publish`:

```python
async def changed_lines(tree: Path, merge_base: str) -> dict[str, set[int]]:
    """Which lines of which files the pull request actually touches, on the
    right-hand side of the diff. An inline comment on anything else makes
    GitHub reject the entire review."""
    diff = await _run(
        "git", "diff", "--unified=0", f"{merge_base}...HEAD", cwd=tree
    )
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
            result.setdefault(current, set())
        elif line.startswith("@@") and current is not None:
            # @@ -old,n +new,m @@
            new_part = line.split("+", 1)[1].split("@@")[0].strip()
            start, _, count = new_part.partition(",")
            first = int(start)
            length = int(count) if count else 1
            result[current].update(range(first, first + length))
    return result


async def post_review(
    repo: str,
    pr_number: int,
    review: dict,
    agent: str,
    model: str,
    tree: Path,
    merge_base: str,
) -> dict:
    """One `COMMENT` review on a pull request.

    `repo` and `pr_number` are the CALLER's, taken from the webhook payload.
    They are never read from `review`, so a findings file cannot redirect a
    review onto a repository the agent chose. That is the highest-consequence
    thing the agent could get wrong, and it is not expressible.

    `event` is the literal "COMMENT". Not a parameter and not a setting: a
    wrong REQUEST_CHANGES blocks a human's work and an APPROVE lets a model
    approve its way into main, and neither is reachable if the verb cannot
    vary.
    """
    qualified = _qualified(repo)
    head_sha = await _run("git", "rev-parse", "HEAD", cwd=tree)

    try:
        existing = await _run(
            "gh", "api", "--paginate",
            f"/repos/{qualified}/pulls/{pr_number}/reviews",
            cwd=tree,
        )
        for entry in json.loads(existing or "[]"):
            if entry.get("commit_id") == head_sha:
                logger.info(
                    "a review for %s#%s at %s already exists; not duplicating",
                    qualified, pr_number, head_sha[:8],
                )
                return {"posted": False, "skipped": True, "url": None}
    except (GitError, FileNotFoundError, json.JSONDecodeError):
        # Not fatal. Failing to READ existing reviews must not stop this one
        # from being posted; the worst case is a duplicate, which the caller
        # also guards against with its own row check.
        logger.warning("could not list existing reviews on %s#%s", qualified, pr_number)

    lines = await changed_lines(tree, merge_base)
    comments, demoted = review_findings.split_comments(review, lines)
    payload = {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": review_findings.build_body(review, agent, model, demoted=demoted),
        "comments": comments,
    }

    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, dir=str(tree)
        ) as handle:
            json.dump(payload, handle)
            body_path = handle.name
        try:
            await _run(
                "gh", "api", "--method", "POST",
                f"/repos/{qualified}/pulls/{pr_number}/reviews",
                "--input", body_path,
                cwd=tree,
            )
        finally:
            Path(body_path).unlink(missing_ok=True)
    except (GitError, FileNotFoundError, OSError) as exc:
        logger.warning("posting a review on %s#%s failed", qualified, pr_number,
                       exc_info=True)
        return {
            "posted": False,
            "skipped": False,
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    return {
        "posted": True,
        "skipped": False,
        "url": f"https://github.com/{qualified}/pull/{pr_number}",
        "comments": len(comments),
        "demoted": len(demoted),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_acp_repo_post_review.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the repo tests together**

Run: `uv run pytest tests/test_acp_repo.py tests/test_acp_repo_review.py tests/test_acp_repo_post_review.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/eve_computer/acp/repo.py tests/test_acp_repo_post_review.py
git commit -m "feat(review): post one COMMENT review from a validated findings file"
```

---

### Task 7: Review sessions on the box

**Files:**
- Modify: `src/eve_computer/acp/session.py:48-55` (`_SYSTEM_HINT`), `:160-186` (`create`), `:283-296` (`close`)
- Modify: `src/eve_computer/app.py` (the `SessionRequest` model and the create route)
- Modify: `Dockerfile.eve-computer`
- Test: `tests/test_acp_session_review.py`

**Interfaces:**
- Consumes: `add_review_worktree` (Task 4), `review.load` (Task 5), `post_review` (Task 6)
- Produces:
  - `session.create(..., kind: str = "code", pr_number: int | None = None, base_ref: str = "main")`
  - `session.close_review(session_id: str) -> dict` returning `{"posted": bool, "findings": int, "url": str | None, "head_sha": str}`
  - `POST /sessions` accepting `kind`, `pr_number`, `base_ref`
  - `POST /sessions/{id}/review` returning the close-review result

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_session_review.py`:

```python
"""A review session is a coding session with a different hint and a
different close. `_spawn` is faked wholesale, exactly as test_acp_session.py
does it: a real ACP subprocess in a unit test is an integration test wearing
the wrong marker.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from eve_computer.acp import session as session_mod
from eve_computer.acp.review import InvalidFindings


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()
    session_mod._semaphore = None
    yield
    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()


def test_the_review_hint_forbids_editing_and_names_the_rubric():
    hint = session_mod.review_hint(["acme/repo"], 7, "abc123")

    assert "review" in hint.lower()
    assert "do not" in hint.lower()
    assert "review.json" in hint
    assert "prompts/code-review" in hint


def test_the_review_hint_requires_naming_skipped_sections():
    """Stack scoping is auditable or it is a hope."""
    hint = session_mod.review_hint(["acme/repo"], 7, "abc123")

    assert "skipped" in hint.lower()


def test_the_coding_hint_is_unchanged():
    """The default must stay exactly what coding sessions already get."""
    assert "do not push" in session_mod._SYSTEM_HINT
    assert "pull request" in session_mod._SYSTEM_HINT


async def test_closing_a_review_reads_the_findings_and_posts(tmp_path, monkeypatch):
    session = session_mod.Session(
        id="s1", agent="claude", model="m", repos=["acme/repo"],
        branch="", directory=tmp_path, kind="review", pr_number=7,
        merge_base="base-sha", head_sha="head-sha",
    )
    session_mod._SESSIONS["s1"] = session
    (tmp_path / "review.json").write_text(json.dumps({
        "summary": "One finding.",
        "findings": [{
            "severity": "nit", "axis": "readability",
            "file": "a.py", "line": 1, "body": "name",
        }],
    }))
    posted = AsyncMock(return_value={"posted": True, "skipped": False,
                                     "url": "https://x/7", "comments": 1,
                                     "demoted": 0})
    monkeypatch.setattr(session_mod.repo, "post_review", posted)
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())

    result = await session_mod.close_review("s1")

    assert result["posted"] is True
    assert result["findings"] == 1
    assert result["url"] == "https://x/7"
    assert posted.await_args.args[0] == "acme/repo"
    assert posted.await_args.args[1] == 7


async def test_a_missing_findings_file_fails_the_session(tmp_path, monkeypatch):
    """It does not fall back to scraping the transcript. A review that
    degrades into a summary looks like a review and is not one."""
    session = session_mod.Session(
        id="s1", agent="claude", model="m", repos=["acme/repo"],
        branch="", directory=tmp_path, kind="review", pr_number=7,
        merge_base="base", head_sha="head",
    )
    session_mod._SESSIONS["s1"] = session
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())

    with pytest.raises(InvalidFindings):
        await session_mod.close_review("s1")

    assert session.status == "failed"


async def test_the_worktree_is_torn_down_even_when_posting_fails(
    tmp_path, monkeypatch
):
    session = session_mod.Session(
        id="s1", agent="claude", model="m", repos=["acme/repo"],
        branch="", directory=tmp_path, kind="review", pr_number=7,
        merge_base="base", head_sha="head",
    )
    session_mod._SESSIONS["s1"] = session
    removed = AsyncMock()
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", removed)

    with pytest.raises(InvalidFindings):
        await session_mod.close_review("s1")

    removed.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_acp_session_review.py -v`
Expected: FAIL with `AttributeError: module 'eve_computer.acp.session' has no attribute 'review_hint'`.

- [ ] **Step 3: Add the review hint and session fields**

In `src/eve_computer/acp/session.py`, add after `_SYSTEM_HINT`:

```python
def review_hint(repos: list[str], pr_number: int, merge_base: str) -> str:
    """The standing instruction for a review session.

    Deliberately not `_SYSTEM_HINT` with a clause bolted on: a reviewer and
    an implementer are told opposite things about editing files, and one
    string trying to say both would be the design going wrong.
    """
    return (
        "You are reviewing a pull request on behalf of an assistant named Eve. "
        f"The repositories are {', '.join(repos)} and the pull request is "
        f"#{pr_number}. The changes under review are `git diff {merge_base}...HEAD`.\n\n"
        "You are REVIEWING, not fixing. Do not edit, create, or delete any "
        "source file, and do not commit anything.\n\n"
        "The review rubric is in `prompts/code-review/` in this repository's "
        "checkout, or at /app/prompts/code-review/ if it is not: SKILL.md is "
        "the five axes and the severity vocabulary, security-checklist.md and "
        "performance-checklist.md are the detailed axes.\n\n"
        "Apply ONLY the rubric sections that match this repository's stack. A "
        "Python service gets no Core Web Vitals findings. Record every section "
        "you skipped, and why, in `skipped_sections`.\n\n"
        "When you are done, write your findings to `review.json` in the "
        "session root directory, with this exact shape:\n"
        '{"summary": "...", "skipped_sections": ["..."], "findings": '
        '[{"severity": "critical|required|optional|nit|fyi", '
        '"axis": "correctness|readability|architecture|security|performance", '
        '"file": "path/from/repo/root.py", "line": 12, "body": "..."}]}\n\n'
        "`line` must be a line the diff actually touches on the right-hand "
        "side, or the finding cannot be anchored. Clean code is a legitimate "
        "outcome: an empty findings list is a valid review."
    )
```

Add to the `Session` dataclass, after `prs`:

```python
    # EVE-27. `kind` is what `close` branches on; the rest are the pull
    # request under review and the two commits that bound its diff.
    kind: str = "code"
    pr_number: int | None = None
    merge_base: str = ""
    head_sha: str = ""
```

- [ ] **Step 4: Branch `create` on kind**

In `session.py`, change the `create` signature and the worktree loop:

```python
async def create(
    session_id: str,
    agent: str,
    model: str,
    repos: list[str],
    prompt: str,
    kind: str = "code",
    pr_number: int | None = None,
    base_ref: str = "main",
) -> Session:
    settings = get_computer_settings()
    argv, env = build(agent, model)

    branch = "" if kind == "review" else f"eve/{repo.slug(prompt)}-{uuid.uuid4().hex[:8]}"
    directory = Path(settings.sessions_dir) / session_id
    directory.mkdir(parents=True, exist_ok=True)

    session = Session(
        id=session_id, agent=agent, model=model, repos=list(repos),
        branch=branch, directory=directory, kind=kind, pr_number=pr_number,
    )
    async with _lock:
        _SESSIONS[session_id] = session

    if kind == "review":
        if pr_number is None:
            raise ValueError("a review session needs a pull request number")
        for name in repos:
            checkout = await repo.add_review_worktree(
                name, directory, pr_number, base_ref
            )
            # One pull request lives in one repository, so the last (and
            # only) checkout's commits are the session's.
            session.merge_base = checkout["merge_base"]
            session.head_sha = checkout["head_sha"]
        hint = review_hint(list(repos), pr_number, session.merge_base)
    else:
        for name in repos:
            await repo.add_worktree(name, directory, branch)
        hint = _SYSTEM_HINT

    await session.prompts.put(prompt)
    session.turns.append(Turn(role="user", text=prompt))
    session.driver = asyncio.create_task(_drive(session, argv, env, hint))
    return session
```

Change `_drive`'s signature to accept the hint and use it:

```python
async def _drive(
    session: Session, argv: list[str], env: dict[str, str], hint: str = _SYSTEM_HINT
) -> None:
```

and inside it, replace the `text_block(_SYSTEM_HINT)` call with `text_block(hint)`.

- [ ] **Step 5: Add `close_review`**

Add to `session.py`, after `close`:

```python
async def close_review(session_id: str) -> dict:
    """A review's ending: read the findings, post them, tear down.

    Raises `InvalidFindings` rather than degrading to an empty review. A
    review that silently becomes "no findings" is worse than no review,
    because it looks like one.
    """
    session = _SESSIONS[session_id]
    try:
        findings = review.load(session.directory)
    except review.InvalidFindings:
        _fail(session, "the review produced no usable review.json")
        raise
    finally:
        if session.driver:
            await session.prompts.put(None)
            session.driver.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session.driver
        with contextlib.suppress(Exception):
            await session.conn.close_session(session_id=session_id)  # type: ignore[union-attr]
        await repo.remove_worktrees(session.directory, session.repos)

    posted = await repo.post_review(
        session.repos[0],
        session.pr_number,
        findings,
        session.agent,
        session.model,
        repo.worktree_path(session.directory, session.repos[0]),
        session.merge_base,
    )
    session.status = "finished"
    return {
        "posted": posted.get("posted", False),
        "skipped": posted.get("skipped", False),
        "error": posted.get("error"),
        "url": posted.get("url"),
        "findings": len(findings["findings"]),
        "counts": _severity_counts(findings),
        "head_sha": session.head_sha,
    }


def _severity_counts(findings: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings["findings"]:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return counts
```

Add the import at the top of `session.py`:

```python
from eve_computer.acp import repo, review
```

**Note:** `post_review` needs the worktree, which `remove_worktrees` has just deleted. Reorder so the post happens before teardown: move the `repo.remove_worktrees` call to after `post_review`, keeping it in a `finally` around both.

- [ ] **Step 6: Expose it on the box's HTTP surface**

In `src/eve_computer/app.py`, extend `SessionRequest`:

```python
class SessionRequest(BaseModel):
    id: str
    agent: str
    model: str
    repos: list[str]
    prompt: str
    # EVE-27. `code` keeps every existing caller working unchanged.
    kind: str = "code"
    pr_number: int | None = None
    base_ref: str = "main"
```

Update the create route's call:

```python
        await session.create(
            body.id, body.agent, body.model, body.repos, body.prompt,
            kind=body.kind, pr_number=body.pr_number, base_ref=body.base_ref,
        )
```

and add `ValueError` to its exception handling, returning 400.

Add a route after `close_session_route`:

```python
@app.post("/sessions/{session_id}/review")
async def close_review_route(
    session_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    _require_session(session_id)
    try:
        return await session.close_review(session_id)
    except InvalidFindings as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

with `from eve_computer.acp.review import InvalidFindings` at the top.

- [ ] **Step 7: Ship the rubric in the image**

In `Dockerfile.eve-computer`, after the `COPY src/eve_computer` line:

```dockerfile
# EVE-27: the review rubric. Vendored and pinned rather than installed at
# build time, so a review is reproducible against a commit. See
# prompts/code-review/SOURCE.md.
COPY prompts/code-review ./prompts/code-review
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_acp_session_review.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 9: Run the existing session and app tests**

Run: `uv run pytest tests/test_acp_session.py tests/test_acp_app.py -v`
Expected: PASS. `create` and `_drive` gained defaulted parameters, so existing callers are unaffected.

- [ ] **Step 10: Commit**

```bash
git add src/eve_computer tests/test_acp_session_review.py Dockerfile.eve-computer
git commit -m "feat(review): review sessions on the box, and ship the rubric in the image"
```

---

### Task 8: Eve dispatches a review

**Files:**
- Create: `src/eve/review/dispatch.py`
- Modify: `src/eve/tools_client.py`
- Test: `tests/test_review_dispatch.py`

**Interfaces:**
- Consumes: `reviewer.choose` (Task 2), `store.create_session` and `review_exists_for` (Task 3), `Settings.review_repos` (Task 1)
- Produces:
  - `tools_client.create_review_session(session_id, agent, model, repo, pr_number, base_ref, prompt) -> str`
  - `tools_client.close_review_session(session_id) -> dict | None`
  - `dispatch.start(repo, pr_number, head_sha, base_ref, member_sub, thread_id) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_dispatch.py`:

```python
"""Starting a review. The allowlist, the idempotence check, and the
reviewer-is-not-the-implementer rule all bind here, before the box is
called and before any tokens are spent."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from eve.review import dispatch


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("EVE_REVIEW_REPOS", '["acme/repo"]')
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_AGENT", "claude")
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_MODEL", "anthropic/claude-sonnet-5")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(dispatch.store, "review_exists_for", AsyncMock(return_value=False))
    monkeypatch.setattr(dispatch.store, "create_session", AsyncMock())
    monkeypatch.setattr(dispatch.store, "implementer_of", AsyncMock(return_value=None))
    monkeypatch.setattr(
        dispatch, "create_review_session", AsyncMock(return_value="ok")
    )


async def test_a_review_is_dispatched_and_recorded():
    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah", thread_id="t1",
    )

    assert not result.startswith("error:")
    dispatch.create_review_session.assert_awaited_once()
    dispatch.store.create_session.assert_awaited_once()
    assert dispatch.store.create_session.await_args.kwargs["kind"] == "review"
    assert dispatch.store.create_session.await_args.kwargs["pr_number"] == 7


async def test_a_repository_outside_the_allowlist_is_refused():
    """A webhook can name any repository; only configured ones spend."""
    result = await dispatch.start(
        repo="someone-else/private", pr_number=1, head_sha="abc",
        base_ref="main", member_sub="sub-noah", thread_id="t1",
    )

    assert result.startswith("error:")
    dispatch.create_review_session.assert_not_awaited()


async def test_an_already_reviewed_commit_is_not_reviewed_again():
    dispatch.store.review_exists_for.return_value = True

    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah", thread_id="t1",
    )

    assert "already" in result.lower()
    dispatch.create_review_session.assert_not_awaited()


async def test_the_row_is_written_only_after_the_box_accepts():
    """Same ordering rule dispatch_coding_task documents: a row for a
    session the box never heard of would be polled forever."""
    dispatch.create_review_session.return_value = "error: eve-computer unavailable"

    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah", thread_id="t1",
    )

    assert result.startswith("error:")
    dispatch.store.create_session.assert_not_awaited()


async def test_an_eve_authored_pull_request_avoids_its_implementer():
    dispatch.store.implementer_of.return_value = ("claude", "anthropic/claude-sonnet-5")

    await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah", thread_id="t1",
    )

    kwargs = dispatch.store.create_session.await_args.kwargs
    assert kwargs["agent"] != "claude"


async def test_review_disabled_refuses_before_anything_is_spent(monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah", thread_id="t1",
    )

    assert result.startswith("error:")
    dispatch.create_review_session.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_review_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.review.dispatch'`.

- [ ] **Step 3: Add the tools_client calls**

In `src/eve/tools_client.py`, after `close_coding_session`:

```python
async def create_review_session(
    session_id: str,
    agent: str,
    model: str,
    repo: str,
    pr_number: int,
    base_ref: str,
    prompt: str,
) -> str:
    body = await _session_request(
        "POST", "/sessions",
        json={
            "id": session_id, "agent": agent, "model": model,
            "repos": [repo], "prompt": prompt, "kind": "review",
            "pr_number": pr_number, "base_ref": base_ref,
        },
    )
    return "ok" if body is not None else "error: eve-computer unavailable"


async def close_review_session(session_id: str) -> dict | None:
    """The review's ending: the box reads the findings and posts them.
    `None` means the box could not be reached or refused, which the
    supervisor reports rather than treating as a clean review."""
    return await _session_request("POST", f"/sessions/{session_id}/review")
```

- [ ] **Step 4: Add the implementer lookup**

In `src/eve/coding/store.py`, at the end:

```python
async def implementer_of(repo: str, head_sha: str) -> tuple[str, str] | None:
    """The `(agent, model)` that opened this pull request, or `None` for a
    human-authored one.

    This is what makes "review with a different model than implemented"
    checkable rather than aspirational: EVE-27's central requirement needs
    to know what wrote the code, and this row is the only record of it.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT agent, model FROM eve_coding_session"
                " WHERE kind = 'code' AND status = 'finished' AND repos ? %s"
                "   AND result -> 'prs' @> %s::jsonb"
                " ORDER BY finished_at DESC LIMIT 1",
                (repo, Jsonb([{"head_sha": head_sha}]).obj),
            )
            row = await cur.fetchone()
            return (row["agent"], row["model"]) if row else None
```

**Note:** if `repo.publish` does not currently record `head_sha` on each PR result, add it there: in `publish`, after the push, capture `await _run("git", "rev-parse", "HEAD", cwd=tree)` into `result["head_sha"]`. Without it this lookup can never match and every Eve PR is treated as human-authored.

- [ ] **Step 5: Write the dispatcher**

Create `src/eve/review/dispatch.py`:

```python
"""Starting a review, with every refusal checked before the box is called.

WHY THE CHECKS ARE HERE AND NOT ON THE BOX. The allowlist, the feature flag,
and the already-reviewed check all decide whether to SPEND, and the spend
starts the moment the box accepts a session. ADR 0006's pattern again: a
denied request never reaches eve-computer at all.

WHY THE ROW IS WRITTEN AFTER THE BOX ACCEPTS. Identical to
`delegate_coding_task`: a row for a session the box never heard of would be
polled forever by the supervisor and eventually reported as stale, for work
that never started.
"""

from __future__ import annotations

import logging
import uuid

from eve.coding import store
from eve.review import reviewer
from eve.settings import get_settings
from eve.tools_client import create_review_session

logger = logging.getLogger(__name__)


def _goal(repo: str, pr_number: int) -> str:
    return f"review {repo}#{pr_number}"


async def start(
    repo: str,
    pr_number: int,
    head_sha: str,
    base_ref: str,
    member_sub: str,
    thread_id: str,
) -> str:
    settings = get_settings()
    if not settings.review_enabled:
        return "error: reviewing is disabled"
    if repo not in settings.review_repos:
        logger.info("refusing to review %s: not in the allowlist", repo)
        return f"error: {repo} is not a repository Eve reviews"

    if await store.review_exists_for(repo, pr_number, head_sha):
        return f"{repo}#{pr_number} at {head_sha[:8]} has already been reviewed"

    implemented_by = await store.implementer_of(repo, head_sha)
    agent, model = reviewer.choose(implemented_by)

    session_id = str(uuid.uuid4())
    goal = _goal(repo, pr_number)
    dispatched = await create_review_session(
        session_id, agent, model, repo, pr_number, base_ref, goal
    )
    if dispatched.startswith("error:"):
        return dispatched

    await store.create_session(
        session_id=session_id,
        member_sub=member_sub,
        thread_id=thread_id,
        goal=goal,
        agent=agent,
        model=model,
        repos=[repo],
        context="",
        kind="review",
        pr_number=pr_number,
        head_sha=head_sha,
    )
    return f"reviewing {repo}#{pr_number} with {agent}/{model}"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_review_dispatch.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 7: Commit**

```bash
git add src/eve/review/dispatch.py src/eve/tools_client.py src/eve/coding/store.py tests/test_review_dispatch.py
git commit -m "feat(review): dispatch a review session with every refusal checked first"
```

---

### Task 9: The GitHub webhook

**Files:**
- Create: `src/eve_ambient/sources/github.py`
- Modify: `src/eve_ambient/app.py`
- Modify: `src/eve_ambient/gates.py:26-34` (`SOURCE_PERMISSION`)
- Modify: `src/eve_ambient/pipeline.py:29` (`_REQUESTED_SOURCES`)
- Test: `tests/test_ambient_sources_github.py`, `tests/test_ambient_app_github.py`

**Interfaces:**
- Consumes: `Family.by_github_login` (Task 1)
- Produces:
  - `github.verify(secret: str, signature: str, body: bytes) -> bool`
  - `github.from_webhook(payload: dict) -> Signal` raising `ValueError`
  - `POST /signals/github`

- [ ] **Step 1: Write the failing signature and payload tests**

Create `tests/test_ambient_sources_github.py`:

```python
"""The webhook's own logic: signature verification and payload shaping."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from eve_ambient.sources import github


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correct_signature_verifies():
    body = b'{"action":"labeled"}'

    assert github.verify("s3cret", _sign("s3cret", body), body) is True


def test_a_wrong_signature_is_rejected():
    body = b'{"action":"labeled"}'

    assert github.verify("s3cret", _sign("wrong", body), body) is False


def test_a_tampered_body_is_rejected():
    signature = _sign("s3cret", b'{"action":"labeled"}')

    assert github.verify("s3cret", signature, b'{"action":"closed"}') is False


def test_an_empty_secret_never_verifies():
    """An unconfigured secret must not become an open door."""
    body = b"{}"

    assert github.verify("", _sign("", body), body) is False


def test_a_malformed_signature_header_is_rejected_not_raised():
    assert github.verify("s3cret", "garbage", b"{}") is False
    assert github.verify("s3cret", "", b"{}") is False


def _payload(action="labeled", login="chalifournoah"):
    return {
        "action": action,
        "number": 7,
        "pull_request": {
            "number": 7,
            "head": {"sha": "abc123"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/acme/repo/pull/7",
        },
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": login},
        "label": {"name": "eve-review"},
    }


def test_a_labeled_event_becomes_a_signal():
    signal = github.from_webhook(_payload())

    assert signal.source == "review"
    assert signal.payload["repo"] == "acme/repo"
    assert signal.payload["pr_number"] == 7
    assert signal.payload["head_sha"] == "abc123"
    assert signal.payload["base_ref"] == "main"


def test_the_dedup_key_is_repo_pr_and_commit():
    """Not the delivery id: a redelivery, a relabel, and two people
    labelling at once all describe the same review of the same code."""
    signal = github.from_webhook(_payload())

    assert signal.key == "acme/repo#7@abc123"


def test_an_unrelated_action_is_refused():
    with pytest.raises(ValueError):
        github.from_webhook(_payload(action="closed"))


def test_an_assigned_event_is_accepted():
    payload = _payload(action="assigned")
    del payload["label"]

    signal = github.from_webhook(payload)

    assert signal.payload["pr_number"] == 7


def test_a_payload_without_a_pull_request_is_refused():
    with pytest.raises(ValueError):
        github.from_webhook({"action": "labeled", "repository": {}})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_ambient_sources_github.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the source module**

Create `src/eve_ambient/sources/github.py`:

```python
"""GitHub's pull-request webhook, verified and shaped into a Signal.

WHY THE RAW BODY. GitHub signs the bytes it sent. Verifying anything else,
including a reserialised parse of them, is the classic bypass: a body that
reparses differently than it hashed passes a check it should fail. So the
route hands this module `bytes`, and parsing happens only after `verify`
returns True.

WHY THE KEY IS (repo, pr, sha) AND NOT THE DELIVERY ID. A redelivered
webhook, a label removed and reapplied, and two people labelling at once all
describe the same review of the same code, and the ambient layer should
collapse them. Including the commit means new commits are genuinely a
different review, which is the one case where a second run is correct.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime

from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

# Only these two commission a review. Everything else GitHub sends, including
# the `ping` on hook creation, is acknowledged and dropped: rejecting a ping
# makes the hook look broken in the GitHub UI.
ACTIONS: tuple[str, ...] = ("labeled", "assigned")


def verify(secret: str, signature: str, body: bytes) -> bool:
    """`X-Hub-Signature-256` against the raw body.

    Fails closed on an unconfigured secret: an empty key must not become an
    open door for anyone who can also send an empty signature.
    """
    if not secret or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature[len("sha256=") :].encode())


def from_webhook(payload: dict) -> Signal:
    action = payload.get("action")
    if action not in ACTIONS:
        raise ValueError(f"action {action!r} does not commission a review")

    pull_request = payload.get("pull_request") or {}
    repository = payload.get("repository") or {}
    repo = repository.get("full_name")
    number = pull_request.get("number")
    head_sha = (pull_request.get("head") or {}).get("sha")
    base_ref = (pull_request.get("base") or {}).get("ref")
    if not (repo and number and head_sha and base_ref):
        raise ValueError("payload names no complete pull request")

    return Signal(
        source="review",
        key=f"{repo}#{number}@{head_sha}",
        occurred_at=datetime.now(UTC),
        # Resolved by the route from `sender.login`, which needs the roster;
        # this module stays pure and does no lookup of its own.
        member_sub="",
        summary=f"{(payload.get('sender') or {}).get('login', 'someone')} "
                f"asked for a review of {repo}#{number}",
        payload={
            "repo": repo,
            "pr_number": number,
            "head_sha": head_sha,
            "base_ref": base_ref,
            "url": pull_request.get("html_url", ""),
            "actor": (payload.get("sender") or {}).get("login", ""),
        },
        cooldown_hours=24,
    )
```

- [ ] **Step 4: Register the permission and the requested-source bypass**

In `src/eve_ambient/gates.py`, add to `SOURCE_PERMISSION`:

```python
    "review": "code.review",
```

In `src/eve_ambient/pipeline.py`, change `_REQUESTED_SOURCES`:

```python
_REQUESTED_SOURCES = ("computer", "coding", "review")
```

- [ ] **Step 5: Write the route tests**

Create `tests/test_ambient_app_github.py`:

```python
"""The webhook route: signature, actor resolution, and the disabled path."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module

SECRET = "hook-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload(login="chalifournoah"):
    return {
        "action": "labeled",
        "pull_request": {
            "number": 7, "head": {"sha": "abc123"}, "base": {"ref": "main"},
            "html_url": "https://github.com/acme/repo/pull/7",
        },
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": login},
        "label": {"name": "eve-review"},
    }


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: ['code.review']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_REVIEW_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()
    app_module._background.clear()
    app_module._in_flight.clear()
    yield
    get_settings.cache_clear()
    app_module._background.clear()
    app_module._in_flight.clear()


@pytest.fixture
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_a_signed_webhook_is_accepted(client, monkeypatch):
    monkeypatch.setattr(app_module, "_handle_in_background", lambda signal: None)
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 202


def test_an_unsigned_webhook_is_rejected(client):
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 401


def test_a_wrongly_signed_webhook_is_rejected(client):
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef",
                 "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 401


def test_an_unknown_actor_gets_no_review(client):
    """A valid signature proves GitHub sent it, not that the labeller may
    spend Eve's tokens."""
    body = json.dumps(_payload(login="a-stranger")).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 403


def test_a_ping_is_acknowledged_not_rejected(client):
    """Rejecting the hook-creation ping makes the hook look broken."""
    body = json.dumps({"zen": "hello"}).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "ping"},
    )

    assert response.status_code == 202


def test_an_unrelated_action_is_acknowledged_and_dropped(client):
    payload = _payload()
    payload["action"] = "closed"
    body = json.dumps(payload).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 202
    assert app_module._background == set()


def test_review_disabled_serves_503(client, monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 503
```

- [ ] **Step 6: Add the route**

In `src/eve_ambient/app.py`, add after `home_assistant_signal`:

```python
@app.post("/signals/github", status_code=202)
async def github_signal(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    """GitHub's pull-request webhook.

    The signature is verified against the RAW body before any parsing: a
    body that reparses differently than it hashed is the classic bypass.
    """
    settings = get_settings()
    body = await request.body()
    if not github.verify(settings.review_webhook_secret, x_hub_signature_256 or "", body):
        logger.warning("rejected github webhook: invalid or missing signature")
        raise HTTPException(status_code=401, detail="unauthorized")

    if x_github_event != "pull_request":
        # Including `ping`, which GitHub sends on hook creation. Rejecting it
        # makes a correctly configured hook look broken in the GitHub UI.
        return {"accepted": None}

    try:
        payload = json.loads(body)
        signal = github.from_webhook(payload)
    except ValueError:
        # An action that does not commission a review is not an error: most
        # pull-request events are `synchronize`, `closed`, and `edited`.
        return {"accepted": None}

    if not settings.review_enabled:
        raise HTTPException(status_code=503, detail="reviewing is disabled")

    try:
        member = get_family().by_github_login(signal.payload["actor"])
    except UnknownMemberError:
        logger.warning(
            "refusing a review for unknown GitHub login %r", signal.payload["actor"]
        )
        raise HTTPException(status_code=403, detail="unknown actor") from None
    signal = replace(signal, member_sub=member.sub)

    dedup_key = (signal.source, signal.key)
    if dedup_key in _in_flight:
        return {"accepted": signal.key}
    _in_flight.add(dedup_key)

    task = asyncio.create_task(_handle_in_background(signal))
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(lambda _task, key=dedup_key: _in_flight.discard(key))
    return {"accepted": signal.key}
```

Add the imports at the top of `app.py`:

```python
import json
from dataclasses import replace

from eve.family import UnknownMemberError, get_family
from eve_ambient.sources import github
```

**Note:** `Signal` must be a frozen dataclass for `replace` to work. Check `src/eve_ambient/types.py`; if it is not a dataclass, construct a new `Signal` with the same fields and `member_sub=member.sub` instead.

- [ ] **Step 7: Run both test files**

Run: `uv run pytest tests/test_ambient_sources_github.py tests/test_ambient_app_github.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 8: Run the ambient suite for regressions**

Run: `uv run pytest tests/test_ambient_app.py tests/test_ambient_pipeline.py tests/test_ambient_gates.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/eve_ambient tests/test_ambient_sources_github.py tests/test_ambient_app_github.py
git commit -m "feat(review): the GitHub webhook, verified against the raw body"
```

---

### Task 10: Supervising and reporting back

**Files:**
- Modify: `src/eve/coding/supervisor.py:63-70` (`_SYSTEM`), `:173-191` (`_advance` close path)
- Create: `src/eve_ambient/sources/review.py`
- Modify: `src/eve_ambient/sources/__init__.py`
- Test: `tests/test_review_supervisor.py`, `tests/test_ambient_sources_review.py`

**Interfaces:**
- Consumes: `close_review_session` (Task 8), `store.recently_resolved_sessions` (existing)
- Produces: `review.poll(member_sub: str) -> list[Signal]`

- [ ] **Step 1: Write the failing supervisor tests**

Create `tests/test_review_supervisor.py`:

```python
"""A review's `done` closes a review, not a coding session."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve.coding import supervisor


def _row(kind="review"):
    return {
        "id": "s1", "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "review acme/repo#7", "repos": ["acme/repo"], "context": "",
        "status": "running", "cursor": 0, "kind": kind, "pr_number": 7,
        "head_sha": "abc", "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC), "supervisor_turns": 0,
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(supervisor.store, "advance_cursor", AsyncMock())
    monkeypatch.setattr(supervisor.store, "bump_supervisor_turns", AsyncMock(return_value=1))
    monkeypatch.setattr(supervisor.store, "mark_resolved", AsyncMock())
    monkeypatch.setattr(supervisor.store, "set_status", AsyncMock())
    monkeypatch.setattr(
        supervisor, "close_review_session",
        AsyncMock(return_value={"posted": True, "findings": 2, "url": "https://x/7",
                                "counts": {"critical": 1, "nit": 1}}),
    )
    monkeypatch.setattr(supervisor, "close_coding_session", AsyncMock(return_value={"prs": []}))


async def test_a_finished_review_closes_the_review_not_a_coding_session(monkeypatch):
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={"status": "idle", "cursor": 1, "pending": [],
                                "turns": [{"role": "agent", "text": "findings written"}]}),
    )
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=supervisor.Decision(action="done", text="reviewed")),
    )
    settings = type("S", (), {
        "coding_session_timeout_seconds": 99999,
        "coding_max_supervisor_turns": 30,
    })()

    result = await supervisor._advance(
        _row(), datetime.now(UTC), supervisor.timedelta(minutes=120), settings
    )

    supervisor.close_review_session.assert_awaited_once_with("s1")
    supervisor.close_coding_session.assert_not_awaited()
    assert result["result"]["findings"] == 2
    assert result["result"]["url"] == "https://x/7"


async def test_a_finished_coding_session_still_closes_normally(monkeypatch):
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={"status": "idle", "cursor": 1, "pending": [],
                                "turns": [{"role": "agent", "text": "done"}]}),
    )
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=supervisor.Decision(action="done", text="done")),
    )
    settings = type("S", (), {
        "coding_session_timeout_seconds": 99999,
        "coding_max_supervisor_turns": 30,
    })()

    await supervisor._advance(
        _row(kind="code"), datetime.now(UTC),
        supervisor.timedelta(minutes=120), settings,
    )

    supervisor.close_coding_session.assert_awaited_once()
    supervisor.close_review_session.assert_not_awaited()


async def test_a_review_that_could_not_be_closed_is_a_failure(monkeypatch):
    """A 422 from the box means no usable review.json. Reporting that as a
    clean review would be the silent degradation the spec forbids."""
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={"status": "idle", "cursor": 1, "pending": [],
                                "turns": [{"role": "agent", "text": "done"}]}),
    )
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=supervisor.Decision(action="done", text="reviewed")),
    )
    supervisor.close_review_session.return_value = None
    settings = type("S", (), {
        "coding_session_timeout_seconds": 99999,
        "coding_max_supervisor_turns": 30,
    })()

    result = await supervisor._advance(
        _row(), datetime.now(UTC), supervisor.timedelta(minutes=120), settings
    )

    assert result["status"] == "failed"


def test_the_review_prompt_tells_the_supervisor_what_done_means():
    prompt = supervisor.system_prompt_for("review")

    assert "review.json" in prompt or "findings" in prompt.lower()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_review_supervisor.py -v`
Expected: FAIL with `AttributeError: module 'eve.coding.supervisor' has no attribute 'system_prompt_for'`.

- [ ] **Step 3: Make the supervisor review-aware**

In `src/eve/coding/supervisor.py`, add after `_SYSTEM`:

```python
_REVIEW_SYSTEM = """You asked a coding agent to REVIEW a pull request and are reading its latest turn.

Decide one of three things:
- reply: it asked something you can answer from the goal or from what you remember about this codebase. Answer briefly and let it keep reviewing.
- done: it has finished reviewing AND written its findings to review.json. Say so.
- escalate: it needs a decision only the family member can make.

`done` means the findings file is written, not that the agent sounded finished. An agent that says it is done without having written review.json is not done: reply and tell it to write the file."""


def system_prompt_for(kind: str) -> str:
    return _REVIEW_SYSTEM if kind == "review" else _SYSTEM
```

In `decide`, replace `f"{_SYSTEM}\n\n"` with:

```python
        f"{system_prompt_for(row.get('kind', 'code'))}\n\n"
```

In `_advance`, replace the final close block:

```python
    if row.get("kind") == "review":
        closed = await close_review_session(row["id"])
        if closed is None:
            result = {"error": "the review produced no usable findings file"}
            await store.mark_resolved(row["id"], "failed", result)
            return _resolved(row, "failed", result, now)
        result = {"summary": decision.text, **closed}
        await store.mark_resolved(row["id"], "finished", result)
        return _resolved(row, "finished", result, now)

    closed = await close_coding_session(row["id"]) or {"prs": []}
    result = {"summary": decision.text, **closed}
    await store.mark_resolved(row["id"], "finished", result)
    return _resolved(row, "finished", result, now)
```

Add `close_review_session` to the `eve.tools_client` import list.

- [ ] **Step 4: Write the ambient source test**

Create `tests/test_ambient_sources_review.py`:

```python
"""Resolved reviews as signals. Mirrors test_ambient_sources_coding.py,
including its 24-hour re-derivation window."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import review


def _session(status="finished", result=None):
    return {
        "id": "s1", "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "review acme/repo#7", "repos": ["acme/repo"], "kind": "review",
        "pr_number": 7, "head_sha": "abc", "status": status,
        "result": result if result is not None else {},
        "finished_at": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(
        review.coding_store, "recently_resolved_sessions", AsyncMock(return_value=[])
    )


async def test_a_posted_review_names_its_counts_and_link():
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"posted": True, "findings": 3, "url": "https://x/7",
                         "counts": {"critical": 1, "nit": 2}})
    ]

    signals = await review.poll("sub-noah")

    assert signals[0].source == "review"
    assert "3" in signals[0].summary
    assert "critical" in signals[0].summary
    assert "https://x/7" in signals[0].summary


async def test_a_clean_review_says_so_rather_than_staying_silent():
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"posted": True, "findings": 0, "url": "https://x/7",
                         "counts": {}})
    ]

    signals = await review.poll("sub-noah")

    assert "no findings" in signals[0].summary.lower()


async def test_a_review_that_could_not_be_posted_still_carries_its_findings():
    """The review took real time and real tokens; losing the findings
    because the post failed would waste all of it."""
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"posted": False, "error": "gh failed", "findings": 2,
                         "counts": {"critical": 2}, "url": None})
    ]

    signals = await review.poll("sub-noah")

    assert "could not" in signals[0].summary.lower()
    assert "2" in signals[0].summary


async def test_a_failed_review_is_reported():
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(status="failed", result={"error": "no usable findings file"})
    ]

    signals = await review.poll("sub-noah")

    assert "no usable findings file" in signals[0].summary


async def test_coding_sessions_are_not_reported_as_reviews():
    """Both kinds live in one table; this source must filter."""
    coding_row = {**_session(), "kind": "code"}
    review.coding_store.recently_resolved_sessions.return_value = [coding_row]

    signals = await review.poll("sub-noah")

    assert signals == []
```

- [ ] **Step 5: Write the source**

Create `src/eve_ambient/sources/review.py`:

```python
"""Resolved review sessions as signals.

Mirrors sources/coding.py, including the 24-hour re-derivation window: a
signal whose delivery was suppressed or deferred is re-derived on a later
tick rather than lost.

`per_member=False` for coding.py's reason: the rows are household-wide and
each carries its own member.

The supervisor's own tick already drives these sessions (app.py's
`_supervise_forever`), so unlike sources/coding.py this one only reads. It
does not call `supervisor.tick()` a second time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eve.coding import store as coding_store
from eve_ambient.types import Signal

_LOOKBACK = timedelta(hours=24)


def _summary(row: dict) -> str:
    goal = row["goal"]
    result = row["result"] or {}

    if row["status"] != "finished":
        return f"A review failed: {goal}. {result.get('error', '')}".rstrip()

    findings = result.get("findings", 0)
    counts = result.get("counts") or {}
    tally = ", ".join(f"{n} {severity}" for severity, n in counts.items())
    url = result.get("url") or ""

    if not result.get("posted"):
        return (
            f"I reviewed {goal} and found {findings} thing(s) ({tally}), but the "
            f"review could not be posted to GitHub: {result.get('error', '')}"
        ).rstrip()
    if findings == 0:
        return f"I reviewed {goal} and found no findings. {url}".strip()
    return f"I reviewed {goal}: {findings} finding(s), {tally}. {url}".strip()


async def poll(_member_sub: str) -> list[Signal]:
    since = datetime.now(UTC) - _LOOKBACK
    rows = await coding_store.recently_resolved_sessions(since=since)

    return [
        Signal(
            source="review",
            key=f"{row['id']}:resolved",
            occurred_at=row["finished_at"],
            member_sub=row["member_sub"],
            summary=_summary(row),
            payload={
                "thread_id": row["thread_id"],
                "goal": row["goal"],
                "repos": row["repos"],
                "pr_number": row.get("pr_number"),
                "result": row["result"],
                "status": row["status"],
            },
            cooldown_hours=24,
        )
        for row in rows
        if row.get("kind") == "review"
    ]
```

- [ ] **Step 6: Register the source**

In `src/eve_ambient/sources/__init__.py`, add the import and the entry:

```python
from eve_ambient.sources import calendar, coding, computer, finances, mail, review
```

```python
    Source("review", False, "code.review", review.poll),
```

**Note:** `sources/coding.py` currently filters nothing by kind. Add `if row.get("kind", "code") == "code"` to its comprehension in the same commit, or a review will be reported twice, once by each source.

- [ ] **Step 7: Run both test files**

Run: `uv run pytest tests/test_review_supervisor.py tests/test_ambient_sources_review.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 8: Run the coding and ambient suites**

Run: `uv run pytest tests/test_coding_supervisor.py tests/test_ambient_sources_coding.py tests/test_ambient_sources.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/eve/coding/supervisor.py src/eve_ambient/sources tests/test_review_supervisor.py tests/test_ambient_sources_review.py
git commit -m "feat(review): supervise reviews and report findings back"
```

---

### Task 11: End-to-end integration

**Files:**
- Create: `tests/test_review_integration.py`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1 to 10
- Produces: nothing

- [ ] **Step 1: Write the integration test**

Create `tests/test_review_integration.py`:

```python
"""Webhook to posted review, with only GitHub and the ACP subprocess faked.

Marked `integration`: it needs the real Postgres from
docker-compose.test.yml for the session row and the idempotence check.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module

pytestmark = pytest.mark.integration

SECRET = "hook-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload():
    return {
        "action": "labeled",
        "pull_request": {
            "number": 7, "head": {"sha": "abc123"}, "base": {"ref": "main"},
            "html_url": "https://github.com/acme/repo/pull/7",
        },
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": "chalifournoah"},
        "label": {"name": "eve-review"},
    }


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: ['code.review']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_REVIEW_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("EVE_REVIEW_REPOS", '["acme/repo"]')
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_a_labelled_pull_request_reaches_dispatch(monkeypatch):
    """The whole trigger path: signature, actor resolution, gate chain,
    dispatch. The box is the only thing faked."""
    from eve.review import dispatch

    started = AsyncMock(return_value="reviewing acme/repo#7 with claude/m")
    monkeypatch.setattr(dispatch, "create_review_session", started)

    body = json.dumps(_payload()).encode()
    with TestClient(app_module.app) as client:
        response = client.post(
            "/signals/github", content=body,
            headers={"X-Hub-Signature-256": _sign(body),
                     "X-GitHub-Event": "pull_request"},
        )

    assert response.status_code == 202


async def test_the_same_commit_labelled_twice_is_reviewed_once(monkeypatch):
    from eve.review import dispatch

    started = AsyncMock(return_value="ok")
    monkeypatch.setattr(dispatch, "create_review_session", started)

    body = json.dumps(_payload()).encode()
    headers = {"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"}
    with TestClient(app_module.app) as client:
        client.post("/signals/github", content=body, headers=headers)
        client.post("/signals/github", content=body, headers=headers)

    assert started.await_count <= 1
```

- [ ] **Step 2: Run it**

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest tests/test_review_integration.py -m integration -v`
Expected: PASS, 2 tests.

- [ ] **Step 3: Document the settings**

In `.env.example`, add:

```bash
# EVE-27: auto-reviewing pull requests. Off by default: this is the only
# path where the public internet reaches a cluster service, and it posts
# comments to GitHub under Eve's own identity.
EVE_REVIEW_ENABLED=false
# The webhook secret configured on the GitHub repository hook. Used as an
# HMAC key over the raw request body, not compared directly.
EVE_REVIEW_WEBHOOK_SECRET=
# JSON list. A webhook can name any repository; only these spend tokens.
EVE_REVIEW_REPOS=[]
# The reviewer for a human-authored pull request, which has no implementing
# model to differ from.
EVE_REVIEW_DEFAULT_AGENT=claude
EVE_REVIEW_DEFAULT_MODEL=anthropic/claude-sonnet-5
```

- [ ] **Step 4: Document the feature**

In `README.md`, add a short section after the coding one:

```markdown
### Reviewing pull requests

Label a pull request `eve-review` (or assign Eve) and she reviews it: a
coding agent in a detached checkout of the PR head, applying the rubric in
`prompts/code-review/`, with findings posted back as a single `COMMENT`
review.

The reviewer is deliberately not the model that wrote the code. For a pull
request Eve opened, the pair is read off the session row that opened it and
avoided; for a human's, `EVE_REVIEW_DEFAULT_AGENT` is used.

Reviews never approve and never request changes. They comment. See
`docs/superpowers/specs/2026-09-22-auto-review-prs-design.md`.
```

- [ ] **Step 5: Run the whole unit suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Run the integration tier**

Run: `uv run pytest -m integration -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_review_integration.py .env.example README.md
git commit -m "test(review): end-to-end trigger path, and document the settings"
```

---

## Deployment checklist

Not code, and not part of any task's definition of done. This is what has to
happen in `home-lab-infrastructure` before the feature does anything:

- [ ] An Ingress for `eve-ambient` with TLS, reachable from GitHub's hook IP ranges.
- [ ] `EVE_REVIEW_WEBHOOK_SECRET` in the deployment's secret, matching the hook.
- [ ] The repository webhook created, event `Pull requests` only.
- [ ] An `eve-review` label created in each allowlisted repository.
- [ ] `EVE_REVIEW_ENABLED=true` and `EVE_REVIEW_REPOS` set.
- [ ] `gh auth` on the box confirmed to have `repo` scope for posting reviews.

## Self-review notes

Checked against the spec, section by section:

- **The rubric** (vendoring, stack scoping): Task 7 Step 3 (the hint requires naming skipped sections), Step 7 (the image `COPY`). The files themselves are already committed with `SOURCE.md`.
- **The trigger** (HMAC on raw body, two actions, dedup key, actor resolution): Task 9.
- **The review session** (detached checkout, merge base, hint parameter, supervisor reuse, `review.json`): Tasks 4, 5, 7, 10.
- **Choosing the reviewer**: Task 2, wired in Task 8.
- **Posting** (`COMMENT` hardcoded, repo from caller, JSON body, demotion, idempotence, failure reported): Task 6.
- **Reporting back**: Task 10.
- **Data model** (`kind`, `pr_number`, `head_sha`, partial index): Task 3.
- **Bounds** (every row of the spec's table): Task 1 (`review_enabled`, `code.review`, allowlist, concurrency, timeout), Task 3 and Task 6 (one review per `head_sha`), Task 6 (`COMMENT` hardcoded).

Two things the spec left open and this plan decides:

1. **`repo.publish` must record `head_sha` per PR result** (Task 8 Step 4), or `implementer_of` can never match and every Eve-authored pull request is treated as human-authored, silently losing the issue's central requirement.
2. **`sources/coding.py` must filter to `kind == "code"`** (Task 10 Step 6), or every review is reported twice.

One deliberate deviation from the spec's prose: the spec says `repo.publish`
labels Eve's own pull requests so the webhook fires. That is left to the
deployment checklist and the open questions rather than implemented, because
it depends on the label existing in each repository first, and a PR labelled
with a label nothing consumes is harmless while the reverse is not.
