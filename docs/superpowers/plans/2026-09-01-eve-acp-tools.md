# ACP tools — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eve delegates coding work to Claude Code, Codex, and OpenCode over the Agent Client Protocol, holds a multi-turn conversation with the agent while it works, and reports back with pull requests.

**Architecture:** `eve-computer` gains a second concurrency lane beside its GUI task queue: ACP sessions, each one an agent subprocess plus one git worktree per repo. The box runs the protocol and makes no judgement. Eve's container holds every judgement — which agent, which model, what to reply, when it is done — in a supervisor loop that ticks inside `eve-ambient`, because that is where the household context lives and the box is deliberately kept ignorant of it.

**Tech Stack:** Python 3.12, `agent-client-protocol` (ACP Python SDK), FastAPI, asyncio subprocesses, psycopg + Alembic, LangChain/LangGraph tools, pytest.

**Spec:** [`docs/superpowers/specs/2026-09-01-eve-acp-tools-design.md`](../specs/2026-09-01-eve-acp-tools-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **ACP protocol version is `1`** — `acp.PROTOCOL_VERSION`, verified against the installed SDK. Do not hardcode the integer; import the constant.
- **`acp.Client` is a `typing.Protocol`**, not an ABC. Subclass it anyway for the type checker; unimplemented methods are not enforced at runtime.
- **`spawn_agent_process` is an `@asynccontextmanager`** yielding `(ClientSideConnection, asyncio.subprocess.Process)`. The connection closes when the context exits, so a live session must hold the context open for its whole life.
- **`PromptResponse.stop_reason`** is exactly one of `"end_turn" | "max_tokens" | "max_turn_requests" | "refusal" | "cancelled"`. No other values exist.
- **The box learns nothing about the family.** Session id, agent name, model name, repo names and prompt text cross the boundary. Never a member subject, name, roster entry, or permission.
- **Eve polls the box; the box never calls Eve.** No callback URL, no webhook, no push. This is load-bearing for `eve-computer`'s NetworkPolicy argument.
- **`ocp/*` models are denied** at dispatch. ADR 0004 probed them live: the proxy strips tool definitions, so the agent answers fluently and changes nothing. Silent failure, therefore a deny-list.
- **Model candidates come from LiteLLM's live catalogue** (`GET /v1/models`), never a static table. Do **not** add a `CODING_MODELS` dict to `src/eve/models.py` — that file owns Eve's five tiers and nothing else.
- **Codex breaks ties.** When neither task nor member preference points anywhere, use `codex`. It rides the subscription.
- **Permission string is `code.delegate`**, checked in Eve's container before any HTTP call (ADR 0006).
- **New dependency goes in the `computer` dependency group**, not `[project.dependencies]`. Only `Dockerfile.eve-computer` runs `uv sync --group computer`; the other four images must not pull it.
- **Unit tests must pass with no network and no services:** `uv run pytest -m "not integration and not live and not docker"`.
- **Commit after every task.** Conventional-commit prefixes, matching this repo's history (`feat(eve-acp): …`, `test(eve-acp): …`, `docs(eve-acp): …`).

---

## File Structure

**Box side — `src/eve_computer/acp/` (new package):**

| File | Responsibility |
|---|---|
| `__init__.py` | Empty. |
| `client.py` | The ACP client half. Auto-approves permission requests; serves `fs/*` confined to the session root. Knows nothing about HTTP, git, or Eve. |
| `registry.py` | Agent name + model → argv and environment. A dict of three, nothing more. |
| `repo.py` | Git only: clone, worktree add/remove, push, `gh pr create`. No ACP types. |
| `session.py` | One live session: subprocess lifetime, state machine, turn log, rolling activity, pending prompts, bounds. The only file that imports both `client` and `repo`. |

**Box side — modified:**

- `src/eve_computer/app.py` — five session routes and a second concurrency lane. `/tasks` untouched.
- `src/eve_computer/settings.py` — session bounds and paths.
- `src/eve_computer/bootstrap.sh` — writes the three model-routing config files.
- `Dockerfile.eve-computer` — `gh`, `codex-acp`, `opencode`, `claude-code-acp`, and the config templates.

**Eve side — `src/eve/coding/` (new package):**

| File | Responsibility |
|---|---|
| `__init__.py` | Empty. |
| `store.py` | Every `eve_coding_session` SQL statement. |
| `catalogue.py` | LiteLLM `/v1/models`, cached, with the `ocp/*` deny-list. |
| `dispatch.py` | The three tools and their permission checks. |
| `supervisor.py` | The control loop: poll, decide, reply/close/park. |

**Eve side — modified:**

- `src/eve/tools_client.py` — the session doors.
- `src/eve/graph.py` — bind the three tools.
- `src/eve/settings.py` — Phase 7 block.
- `src/eve_ambient/app.py` — the supervisor loop.
- `src/eve_ambient/sources/coding.py` — new ambient source.
- `src/eve_ambient/gates.py` — `coding` permission mapping.
- `src/eve_ambient/pipeline.py` — relevance-filter bypass.
- `alembic/versions/0005_eve_coding_session.py` — the table.
- `family.yaml` — `code.delegate`.

---

## Task 1: The ACP client half

The box's side of the protocol. This is the only file that implements what the *agent* calls back into, and it is deliberately ignorant of HTTP, git, and Eve.

**Files:**
- Create: `src/eve_computer/acp/__init__.py`
- Create: `src/eve_computer/acp/client.py`
- Modify: `pyproject.toml` (the `computer` dependency group)
- Test: `tests/test_acp_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `SessionClient(root: Path, on_update: Callable[[object], None])` — an `acp.Client`.
  - `SessionClient.session_update(session_id: str, update, **kwargs) -> None`
  - `SessionClient.request_permission(session_id, tool_call, options, **kwargs) -> RequestPermissionResponse`
  - `SessionClient.read_text_file(session_id, path, line=None, limit=None, **kwargs) -> ReadTextFileResponse`
  - `SessionClient.write_text_file(session_id, path, content, **kwargs) -> WriteTextFileResponse`
  - `PathEscapedRoot` — exception raised when a path resolves outside `root`.

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`. The `computer` group currently reads:

```toml
computer = [
    "claude-agent-sdk>=0.2.149",
]
```

Change it to:

```toml
computer = [
    "claude-agent-sdk>=0.2.149",
    # The ACP client half of EVE-4. Kept in this group for the same reason
    # claude-agent-sdk is: only Dockerfile.eve-computer runs `uv sync --group
    # computer`, and eve-sandbox's whole security argument (ADR 0010) rests on
    # its image containing nothing it does not need.
    "agent-client-protocol>=0.5.0",
]
```

Then run `uv sync --group computer` and confirm `uv run --group computer python -c "import acp; print(acp.PROTOCOL_VERSION)"` prints `1`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_acp_client.py`:

```python
"""The box's side of the protocol. Confinement is the load-bearing part:
the agent has a shell and could ask for any path, and `fs/*` must not become
a second, wider door than the worktree it was given."""

from pathlib import Path

import pytest
from acp.schema import (
    AllowedOutcome,
    PermissionOption,
    ToolCallUpdate,
)

from eve_computer.acp.client import PathEscapedRoot, SessionClient


def _client(tmp_path: Path, updates: list | None = None) -> SessionClient:
    return SessionClient(root=tmp_path, on_update=(updates if updates is None else updates.append))


async def test_permission_requests_are_auto_approved(tmp_path):
    client = _client(tmp_path, [])
    options = [
        PermissionOption(option_id="no", name="Reject", kind="reject_once"),
        PermissionOption(option_id="yes", name="Allow", kind="allow_always"),
    ]

    response = await client.request_permission(
        session_id="s1", tool_call=ToolCallUpdate(tool_call_id="t1"), options=options
    )

    assert isinstance(response.outcome, AllowedOutcome)
    assert response.outcome.option_id == "yes"


async def test_permission_is_denied_when_no_allow_option_is_offered(tmp_path):
    client = _client(tmp_path, [])
    options = [PermissionOption(option_id="no", name="Reject", kind="reject_once")]

    response = await client.request_permission(
        session_id="s1", tool_call=ToolCallUpdate(tool_call_id="t1"), options=options
    )

    assert response.outcome.outcome == "cancelled"


async def test_reading_a_file_inside_the_root_returns_its_content(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    client = _client(tmp_path, [])

    response = await client.read_text_file(session_id="s1", path="a.txt")

    assert response.content == "hello"


async def test_reading_honours_line_and_limit(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\nfour\n")
    client = _client(tmp_path, [])

    response = await client.read_text_file(session_id="s1", path="a.txt", line=2, limit=2)

    assert response.content == "two\nthree\n"


async def test_writing_a_file_creates_missing_parents(tmp_path):
    client = _client(tmp_path, [])

    await client.write_text_file(session_id="s1", path="deep/nested/a.txt", content="x")

    assert (tmp_path / "deep" / "nested" / "a.txt").read_text() == "x"


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "sub/../../outside.txt"])
async def test_paths_outside_the_root_are_refused(tmp_path, path):
    client = _client(tmp_path, [])

    with pytest.raises(PathEscapedRoot):
        await client.read_text_file(session_id="s1", path=path)
    with pytest.raises(PathEscapedRoot):
        await client.write_text_file(session_id="s1", path=path, content="x")


async def test_a_symlink_pointing_out_of_the_root_is_refused(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    (tmp_path / "link.txt").symlink_to(outside)
    client = _client(tmp_path, [])

    with pytest.raises(PathEscapedRoot):
        await client.read_text_file(session_id="s1", path="link.txt")


async def test_session_updates_are_handed_to_the_callback(tmp_path):
    updates: list = []
    client = _client(tmp_path, updates)

    await client.session_update(session_id="s1", update={"session_update": "agent_message_chunk"})

    assert updates == [{"session_update": "agent_message_chunk"}]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --group computer pytest tests/test_acp_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve_computer.acp'`

- [ ] **Step 4: Write the implementation**

Create `src/eve_computer/acp/__init__.py` (empty file), then `src/eve_computer/acp/client.py`:

```python
"""The box's side of the Agent Client Protocol.

Deliberately ignorant of HTTP, git, and Eve: this file answers what the
*agent* calls back into, and nothing else. `session.py` composes it with
the rest.

Two decisions worth reading before changing anything here.

`request_permission` auto-approves. The design doc's "Oversight" section is
explicit that there is no per-action gate on this box - dispatching is the
gate, the pod and the NetworkPolicy are the boundary, and the pull request
is the review. An approval prompt over a control channel with nobody
listening is not a boundary, it is a hang. When the agent offers no
allow-shaped option at all we return `cancelled` rather than inventing one:
refusing a menu we were not given is the honest answer.

`_resolve` confines every `fs/*` path to the session root, symlinks
included. The agent already has a shell and can read what it likes with it;
the point is not to contain the machine (the pod does that) but to stop
`fs/*` quietly becoming a second, wider door than the worktree the session
was handed. `Path.resolve()` before the check is what makes `..` and
symlinks both fall out of one comparison.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from acp import Client
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    WriteTextFileResponse,
)

logger = logging.getLogger(__name__)

_ALLOW_KINDS = ("allow_always", "allow_once")


class PathEscapedRoot(Exception):
    """An `fs/*` path resolved outside the session root."""


class SessionClient(Client):
    def __init__(self, root: Path, on_update: Callable[[Any], None]) -> None:
        self._root = Path(root).resolve()
        self._on_update = on_update

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        absolute = candidate if candidate.is_absolute() else self._root / candidate
        # strict=False: a write to a file that does not exist yet still has to
        # be resolved and checked, not rejected for being new.
        resolved = absolute.resolve(strict=False)
        if resolved != self._root and self._root not in resolved.parents:
            raise PathEscapedRoot(f"{path!r} resolves outside the session root")
        return resolved

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._on_update(update)

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        for option in options:
            if option.kind in _ALLOW_KINDS:
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", option_id=option.option_id)
                )
        logger.warning("no allow-shaped permission option offered; denying")
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        text = self._resolve(path).read_text()
        if line is None and limit is None:
            return ReadTextFileResponse(content=text)
        lines = text.splitlines(keepends=True)
        start = (line - 1) if line else 0
        end = (start + limit) if limit else None
        return ReadTextFileResponse(content="".join(lines[start:end]))

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> WriteTextFileResponse:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return WriteTextFileResponse()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group computer pytest tests/test_acp_client.py -v`
Expected: PASS (9 tests, counting the 3 parametrised cases)

- [ ] **Step 6: Confirm nothing else broke**

Run: `uv run pytest -m "not integration and not live and not docker" -q`
Expected: the pre-existing suite still passes. `tests/test_acp_client.py` will be skipped or error here if `acp` is not in the default sync — if so, that is expected and it is covered by the `--group computer` run above.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/eve_computer/acp/ tests/test_acp_client.py
git commit -m "feat(eve-acp): add the ACP client half, confined to the session root"
```

---

## Task 2: The agent registry

Agent name plus model in, argv and environment out. Three entries.

**Files:**
- Create: `src/eve_computer/acp/registry.py`
- Modify: `src/eve_computer/settings.py`
- Test: `tests/test_acp_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `AGENT_NAMES: tuple[str, ...]` — `("codex", "claude", "opencode")`
  - `build(agent: str, model: str) -> tuple[list[str], dict[str, str]]` — argv and the environment overlay for the subprocess.
  - `UnknownAgent` — exception.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_registry.py`:

```python
import pytest

from eve_computer.acp.registry import AGENT_NAMES, UnknownAgent, build


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_BASE_URL", "https://litellm.example")
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_API_KEY", "sk-test")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    yield
    get_computer_settings.cache_clear()


def test_every_named_agent_builds():
    for agent in AGENT_NAMES:
        argv, env = build(agent, "some/model")
        assert argv and isinstance(argv[0], str)
        assert env


def test_codex_is_first_because_it_breaks_ties():
    assert AGENT_NAMES[0] == "codex"


def test_the_model_reaches_every_agent():
    for agent in AGENT_NAMES:
        argv, env = build(agent, "chatgpt/gpt-5.6-luna")
        assert "chatgpt/gpt-5.6-luna" in [*argv, *env.values()]


def test_claude_is_routed_through_litellms_anthropic_door():
    _argv, env = build("claude", "anthropic/claude-sonnet-5")
    assert env["ANTHROPIC_BASE_URL"] == "https://litellm.example"
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["ANTHROPIC_MODEL"] == "anthropic/claude-sonnet-5"


def test_codex_and_opencode_carry_the_litellm_key():
    for agent in ("codex", "opencode"):
        _argv, env = build(agent, "chatgpt/gpt-5.6-sol")
        assert env["LITELLM_API_KEY"] == "sk-test"


def test_an_unknown_agent_is_refused():
    with pytest.raises(UnknownAgent):
        build("cursor", "chatgpt/gpt-5.6-sol")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group computer pytest tests/test_acp_registry.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve_computer.acp.registry'`

- [ ] **Step 3: Extend the settings**

Edit `src/eve_computer/settings.py`. Add these fields to `ComputerSettings`, after `tasks_dir`:

```python
    # EVE-4 (ACP tools). Sessions are a second lane beside the GUI task
    # queue: they need no X display, so serialising them behind the one
    # mouse would be a bound with no reason behind it.
    sessions_dir: str = "/home/eve/sessions"
    code_dir: str = "/home/eve/code"
    max_concurrent_sessions: int = 3
    session_max_turns: int = 40
    session_turn_timeout_seconds: int = 1800
    session_timeout_seconds: int = 14400
    github_owner: str = ""
```

- [ ] **Step 4: Write the implementation**

Create `src/eve_computer/acp/registry.py`:

```python
"""Agent name + model in, argv + environment out. Three entries in a dict.

No plugin system and no abstract base class: ACP exists precisely so that
the second and third harness are a line of config rather than a second
integration, and a registry with three entries that grows a factory is a
registry that has forgotten why it was cheap.

The three route to LiteLLM three different ways and there is no honest way
to flatten that. Claude Code speaks Anthropic Messages and reads
ANTHROPIC_BASE_URL from its own environment; Codex and OpenCode read a
provider block from a config file `bootstrap.sh` writes (Task 6) and take
only the key here. Pretending one mechanism served all three would mean
inventing an abstraction over exactly the part that differs.
"""

from __future__ import annotations

from eve_computer.settings import get_computer_settings

# Order is load-bearing only in that `codex` is first: it rides the ChatGPT
# subscription, so it is what Eve falls back to when nothing in the task or
# the member's preferences points anywhere (spec: "Codex breaks ties").
AGENT_NAMES: tuple[str, ...] = ("codex", "claude", "opencode")


class UnknownAgent(Exception):
    """An agent name outside AGENT_NAMES."""


def build(agent: str, model: str) -> tuple[list[str], dict[str, str]]:
    if agent not in AGENT_NAMES:
        raise UnknownAgent(f"unknown agent {agent!r}; expected one of {AGENT_NAMES}")
    settings = get_computer_settings()

    if agent == "claude":
        return (
            ["claude-code-acp"],
            {
                "ANTHROPIC_BASE_URL": settings.litellm_base_url,
                "ANTHROPIC_API_KEY": settings.litellm_api_key,
                "ANTHROPIC_MODEL": model,
            },
        )
    if agent == "codex":
        return (
            ["codex-acp", "--model", model],
            {"LITELLM_API_KEY": settings.litellm_api_key},
        )
    return (
        ["opencode", "acp", "--model", model],
        {"LITELLM_API_KEY": settings.litellm_api_key},
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group computer pytest tests/test_acp_registry.py tests/test_computer_settings.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve_computer/acp/registry.py src/eve_computer/settings.py tests/test_acp_registry.py
git commit -m "feat(eve-acp): add the three-agent registry and session settings"
```

---
## Task 3: Git — clones, worktrees, and pull requests

Git only. No ACP types reach this file. Worktrees are what let two sessions work the same repository at once without fighting over one checkout.

**Files:**
- Create: `src/eve_computer/acp/repo.py`
- Test: `tests/test_acp_repo.py`

**Interfaces:**
- Consumes: `eve_computer.settings.get_computer_settings` (`code_dir`, `sessions_dir`, `github_owner`).
- Produces:
  - `GitError` — exception carrying the failing command and stderr.
  - `async ensure_clone(repo: str) -> Path`
  - `async add_worktree(repo: str, session_dir: Path, branch: str) -> Path`
  - `async publish(session_dir: Path, repos: list[str], branch: str) -> list[dict]` — each dict is `{"repo": str, "commits": int, "pr_url": str | None}`
  - `async remove_worktrees(session_dir: Path, repos: list[str]) -> None`
  - `slug(text: str) -> str` — branch-safe slug for a goal.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_repo.py`:

```python
"""Real git against a real temporary origin. Mocking git here would test
the mock: worktree semantics, `origin/HEAD`, and "did this branch actually
gain commits" are exactly the parts that would break in production while a
mocked test stayed green.

`gh` is the one thing faked, via a script on PATH - there is no GitHub in a
unit test, and the code's contract with `gh` is one line of stdout.
"""

import os
import subprocess
from pathlib import Path

import pytest

from eve_computer.acp import repo as repo_mod
from eve_computer.acp.repo import (
    GitError,
    add_worktree,
    ensure_clone,
    publish,
    remove_worktrees,
    slug,
)


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path):
    """A bare origin with one commit on `main`."""
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
    return bare


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A `gh` on PATH that prints a PR URL and records its argv."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'echo "https://github.com/acme/repo/pull/1"\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


@pytest.fixture(autouse=True)
def _settings(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_GITHUB_OWNER", "acme")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    # `ensure_clone` builds an https URL from the repo name; point it at the
    # temporary bare repo instead so no network is involved.
    monkeypatch.setattr(repo_mod, "_clone_url", lambda name: str(origin))
    yield
    get_computer_settings.cache_clear()


def test_slug_is_branch_safe():
    assert slug("Fix the CalDAV client's 500!") == "fix-the-caldav-client-s-500"
    assert len(slug("x " * 200)) <= 40


async def test_ensure_clone_creates_the_clone_once_and_then_fetches(tmp_path):
    first = await ensure_clone("acme/repo")
    assert (first / ".git").exists()
    second = await ensure_clone("acme/repo")
    assert first == second


async def test_add_worktree_checks_out_a_new_branch_under_the_session(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")

    assert tree == session_dir / "repo"
    assert (tree / "README.md").read_text() == "hello\n"
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tree, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == "eve/fix-1"


async def test_two_sessions_get_independent_worktrees_of_one_repo(tmp_path):
    a = await add_worktree("acme/repo", tmp_path / "sessions" / "a", "eve/a-1")
    b = await add_worktree("acme/repo", tmp_path / "sessions" / "b", "eve/b-1")

    (a / "only-a.txt").write_text("a")
    assert not (b / "only-a.txt").exists()


async def test_publish_opens_a_pr_for_a_repo_with_commits(tmp_path, fake_gh):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")
    (tree / "new.txt").write_text("x")
    _run("git", "add", "new.txt", cwd=tree)
    _run("git", "-c", "user.email=e@x", "-c", "user.name=E", "commit", "-m", "add new", cwd=tree)

    results = await publish(session_dir, ["acme/repo"], "eve/fix-1")

    assert results == [
        {"repo": "acme/repo", "commits": 1, "pr_url": "https://github.com/acme/repo/pull/1"}
    ]
    assert "pr create" in fake_gh.read_text()


async def test_publish_opens_no_pr_for_a_repo_with_no_commits(tmp_path, fake_gh):
    session_dir = tmp_path / "sessions" / "s1"
    await add_worktree("acme/repo", session_dir, "eve/fix-1")

    results = await publish(session_dir, ["acme/repo"], "eve/fix-1")

    assert results == [{"repo": "acme/repo", "commits": 0, "pr_url": None}]
    assert not fake_gh.exists()


async def test_publish_reports_a_failed_pr_without_losing_the_other_repos(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")
    (tree / "new.txt").write_text("x")
    _run("git", "add", "new.txt", cwd=tree)
    _run("git", "-c", "user.email=e@x", "-c", "user.name=E", "commit", "-m", "add new", cwd=tree)
    # No `gh` on PATH at all: the push succeeds, the PR does not.
    monkeypatch.setenv("PATH", "/nonexistent")

    results = await publish(session_dir, ["acme/repo"], "eve/fix-1")

    assert results[0]["commits"] == 1
    assert results[0]["pr_url"] is None
    assert "error" in results[0]


async def test_remove_worktrees_leaves_the_branch_behind(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")

    await remove_worktrees(session_dir, ["acme/repo"])

    assert not tree.exists()
    clone = await ensure_clone("acme/repo")
    branches = subprocess.run(
        ["git", "branch", "--list", "eve/fix-1"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout
    assert "eve/fix-1" in branches


async def test_a_failing_git_command_raises_with_its_stderr(tmp_path):
    with pytest.raises(GitError) as excinfo:
        await add_worktree("acme/repo", tmp_path / "sessions" / "s1", "refs/heads/")
    assert "git" in str(excinfo.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group computer pytest tests/test_acp_repo.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve_computer.acp.repo'`

- [ ] **Step 3: Write the implementation**

Create `src/eve_computer/acp/repo.py`:

```python
"""Clones, worktrees, and pull requests. Git only - no ACP type reaches
this file, and no git command leaves it.

WHY WORKTREES. One clone per repo, one worktree per repo per session. Two
sessions asked to touch the same repository would otherwise fight over one
checkout's HEAD, and serialising them to avoid that would be a bound with
no reason behind it - they need no shared display, unlike the GUI queue.

WHY THE BOX OPENS THE PR, NOT THE AGENT. `gh` is on PATH and the agent
could run it, but then whether Eve gets a URL depends on whether the agent
remembered. Doing it here is deterministic, and `--fill` means the agent's
own commit messages still become the PR body, so nothing it authored is
lost. A repo with no commits gets no PR rather than an empty one.

WHY A FAILED PR IS NOT AN EXCEPTION. `publish` is called once, at the end
of a session that may have taken half an hour across several repos. A
`gh` failure on the second repo must not discard the first repo's result,
so each repo's outcome is a dict and the failure rides in it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

_SLUG_MAX = 40


class GitError(Exception):
    """A git (or gh) invocation exited non-zero."""


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:_SLUG_MAX].rstrip("-")


def _qualified(repo: str) -> str:
    """`repo` -> `owner/repo`, using the configured owner."""
    if "/" in repo:
        return repo
    owner = get_computer_settings().github_owner
    return f"{owner}/{repo}" if owner else repo


def _clone_url(repo: str) -> str:
    # `gh auth login` installs a credential helper, so https needs no token
    # in the URL and none is ever written to disk by this code.
    return f"https://github.com/{_qualified(repo)}.git"


def clone_path(repo: str) -> Path:
    return Path(get_computer_settings().code_dir) / _qualified(repo)


def worktree_path(session_dir: Path, repo: str) -> Path:
    return Path(session_dir) / _qualified(repo).split("/")[-1]


async def _run(*args: str, cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise GitError(f"{' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def ensure_clone(repo: str) -> Path:
    path = clone_path(repo)
    if (path / ".git").exists():
        await _run("git", "fetch", "--prune", "origin", cwd=path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    await _run("git", "clone", _clone_url(repo), str(path))
    return path


async def _base_ref(clone: Path) -> str:
    """The remote default branch, as a ref this repo can diff against."""
    try:
        return await _run("git", "rev-parse", "--abbrev-ref", "origin/HEAD", cwd=clone)
    except GitError:
        # A bare origin created without a symbolic HEAD, or a clone made
        # before one existed. Ask the remote directly rather than guessing
        # between `main` and `master`.
        head = await _run("git", "remote", "show", "origin", cwd=clone)
        for line in head.splitlines():
            if "HEAD branch:" in line:
                return f"origin/{line.split(':', 1)[1].strip()}"
        raise


async def add_worktree(repo: str, session_dir: Path, branch: str) -> Path:
    clone = await ensure_clone(repo)
    base = await _base_ref(clone)
    tree = worktree_path(session_dir, repo)
    tree.parent.mkdir(parents=True, exist_ok=True)
    await _run("git", "worktree", "add", "-b", branch, str(tree), base, cwd=clone)
    return tree


async def publish(session_dir: Path, repos: list[str], branch: str) -> list[dict]:
    results: list[dict] = []
    for repo in repos:
        tree = worktree_path(session_dir, repo)
        result: dict = {"repo": _qualified(repo), "commits": 0, "pr_url": None}
        try:
            clone = clone_path(repo)
            base = await _base_ref(clone)
            count = await _run("git", "rev-list", "--count", f"{base}..HEAD", cwd=tree)
            result["commits"] = int(count)
            if result["commits"] == 0:
                results.append(result)
                continue
            await _run("git", "push", "-u", "origin", branch, cwd=tree)
            result["pr_url"] = await _run(
                "gh", "pr", "create", "--fill", "--head", branch, cwd=tree
            ).strip() or None
        except (GitError, FileNotFoundError, ValueError) as exc:
            logger.warning("publishing %s failed", repo, exc_info=True)
            result["error"] = f"{exc.__class__.__name__}: {exc}"
        results.append(result)
    return results


async def remove_worktrees(session_dir: Path, repos: list[str]) -> None:
    """Tears down the checkouts, never the branches. A session's work
    survives its worktree - the branch is on the clone and, once pushed, on
    the remote."""
    for repo in repos:
        try:
            await _run(
                "git", "worktree", "remove", "--force",
                str(worktree_path(session_dir, repo)),
                cwd=clone_path(repo),
            )
        except GitError:
            logger.warning("could not remove worktree for %s", repo, exc_info=True)
```

Note: `_run` returns `str` already stripped, so the `.strip()` after the `gh` call is redundant — remove it and write:

```python
            result["pr_url"] = await _run(
                "gh", "pr", "create", "--fill", "--head", branch, cwd=tree
            ) or None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group computer pytest tests/test_acp_repo.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/eve_computer/acp/repo.py tests/test_acp_repo.py
git commit -m "feat(eve-acp): add clone, worktree, and pull-request handling"
```

---

## Task 4: The session

The one file that composes the client, the registry, and git. It owns the state machine the whole design turns on — and specifically, it never decides what an `idle` turn *means*.

**Files:**
- Create: `src/eve_computer/acp/session.py`
- Test: `tests/test_acp_session.py`

**Interfaces:**
- Consumes: `SessionClient` (Task 1), `build`/`AGENT_NAMES`/`UnknownAgent` (Task 2), `add_worktree`/`publish`/`remove_worktrees`/`slug` (Task 3).
- Produces:
  - `Session` dataclass with fields `id, agent, model, repos, branch, status, turns, activity, pending, error`.
  - `async create(session_id, agent, model, repos, prompt) -> Session`
  - `async get(session_id) -> Session | None`
  - `async send(session_id, text) -> None`
  - `async close(session_id) -> dict` — `{"prs": [...]}`
  - `async kill(session_id) -> None`
  - `def snapshot(session, since: int) -> dict` — `{"status", "activity", "turns", "pending", "cursor", "error"}`
  - `_SESSIONS: dict[str, Session]` — module-level, for tests to inspect.
  - Statuses: `"queued" | "running" | "idle" | "finished" | "failed" | "killed"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_session.py`:

```python
"""The state machine, with the agent subprocess faked at the `spawn` seam.

The point of every test here is the same: the box records what happened and
NEVER classifies it. An `idle` session with the agent asking a question and
an `idle` session with the work finished are the same state on this side of
the boundary - Eve's container is what tells them apart, because it is the
only side that knows anything about the family.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from eve_computer.acp import session as session_mod
from eve_computer.acp.session import close, create, get, kill, send, snapshot


class FakeConn:
    """Stands in for acp.ClientSideConnection."""

    def __init__(self, stop_reasons=None, reply="ok"):
        self.prompts: list[str] = []
        self.cancelled = False
        self.closed = False
        self._stop_reasons = list(stop_reasons or [])
        self._reply = reply
        self.client = None

    async def initialize(self, **kwargs):
        return type("R", (), {"agent_capabilities": None})()

    async def new_session(self, **kwargs):
        return type("R", (), {"session_id": "acp-1"})()

    async def prompt(self, session_id, prompt, **kwargs):
        self.prompts.append(prompt[0].text)
        if self.client is not None:
            self.client._on_update(
                type("U", (), {"session_update": "agent_message_chunk",
                               "content": type("C", (), {"type": "text", "text": self._reply})()})()
            )
        reason = self._stop_reasons.pop(0) if self._stop_reasons else "end_turn"
        return type("R", (), {"stop_reason": reason})()

    async def cancel(self, session_id, **kwargs):
        self.cancelled = True

    async def close_session(self, session_id, **kwargs):
        self.closed = True


@pytest.fixture
def fake_spawn(monkeypatch):
    conns: list[FakeConn] = []

    class NullManager:
        """Stands in for the spawn_agent_process context manager, which
        session.py closes in its `finally`."""

        async def __aexit__(self, *exc):
            return False

    def _install(conn: FakeConn):
        async def _spawn(client, argv, env, cwd):
            conn.client = client
            conns.append(conn)
            return conn, NullManager()
        monkeypatch.setattr(session_mod, "_spawn", _spawn)
        return conn

    return _install


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_SESSION_MAX_TURNS", "3")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()

    async def _add_worktree(repo, session_dir, branch):
        tree = Path(session_dir) / repo.split("/")[-1]
        tree.mkdir(parents=True, exist_ok=True)
        return tree

    monkeypatch.setattr(session_mod.repo, "add_worktree", _add_worktree)
    monkeypatch.setattr(
        session_mod.repo, "publish",
        AsyncMock(return_value=[{"repo": "acme/repo", "commits": 1, "pr_url": "u"}]),
    )
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())
    yield
    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()


async def _settle():
    """Let the session's background task run to its next await point."""
    for _ in range(50):
        await asyncio.sleep(0)


async def test_a_finished_turn_leaves_the_session_idle_not_finished(fake_spawn):
    fake_spawn(FakeConn(reply="Which auth library do you want?"))

    await create("s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "add auth")
    await _settle()

    assert get("s1").status == "idle"


async def test_the_agents_reply_is_recorded_as_a_turn(fake_spawn):
    fake_spawn(FakeConn(reply="opened a PR"))

    await create("s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "add auth")
    await _settle()

    turns = snapshot(get("s1"), since=0)["turns"]
    assert turns[0]["role"] == "user" and turns[0]["text"] == "add auth"
    assert turns[1]["role"] == "agent" and "opened a PR" in turns[1]["text"]


async def test_the_cursor_returns_only_new_turns(fake_spawn):
    conn = fake_spawn(FakeConn())

    await create("s1", "codex", "m", ["acme/repo"], "first")
    await _settle()
    first = snapshot(get("s1"), since=0)
    await send("s1", "second")
    await _settle()

    later = snapshot(get("s1"), since=first["cursor"])
    assert [t["text"] for t in later["turns"] if t["role"] == "user"] == ["second"]
    assert conn.prompts == ["first", "second"]


async def test_activity_is_a_rolling_window_not_a_transcript(fake_spawn):
    fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()
    session = get("s1")

    for i in range(50):
        session_mod._record_activity(session, f"tool call {i}")

    assert len(session.activity) <= session_mod._ACTIVITY_MAX
    assert session.activity[-1] == "tool call 49"


async def test_a_pending_message_is_reported_and_not_auto_sent(fake_spawn):
    conn = fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    await session_mod.enqueue("s1", "use httpx instead")

    assert snapshot(get("s1"), since=0)["pending"] == ["use httpx instead"]
    # The BOX does not decide to deliver it - Eve composes the next prompt.
    assert conn.prompts == ["go"]


async def test_exceeding_max_turns_fails_the_session(fake_spawn):
    fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()
    for _ in range(2):
        await send("s1", "again")
        await _settle()

    await send("s1", "once more")
    await _settle()

    session = get("s1")
    assert session.status == "failed"
    assert "max turns" in session.error


async def test_a_refusal_stop_reason_fails_the_session(fake_spawn):
    fake_spawn(FakeConn(stop_reasons=["refusal"]))

    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    assert get("s1").status == "failed"
    assert "refusal" in get("s1").error


async def test_close_publishes_and_reports_the_prs(fake_spawn):
    conn = fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    result = await close("s1")

    assert result["prs"] == [{"repo": "acme/repo", "commits": 1, "pr_url": "u"}]
    assert get("s1").status == "finished"
    assert conn.closed


async def test_kill_cancels_the_agent_and_keeps_the_worktrees(fake_spawn):
    conn = fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    await kill("s1")
    await _settle()

    assert get("s1").status == "killed"
    assert conn.cancelled


async def test_an_unknown_agent_is_refused_at_create(fake_spawn):
    from eve_computer.acp.registry import UnknownAgent

    with pytest.raises(UnknownAgent):
        await create("s1", "cursor", "m", ["acme/repo"], "go")


async def test_the_branch_name_is_shared_across_every_repo(fake_spawn):
    fake_spawn(FakeConn())

    await create("s1", "codex", "m", ["acme/one", "acme/two"], "cross-repo change")
    await _settle()

    session = get("s1")
    assert session.branch.startswith("eve/cross-repo-change-")
    assert session.repos == ["acme/one", "acme/two"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group computer pytest tests/test_acp_session.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve_computer.acp.session'`

- [ ] **Step 3: Write the implementation**

Create `src/eve_computer/acp/session.py`:

```python
"""One live ACP session: subprocess, worktrees, turn log, bounds.

THE ONE RULE IN THIS FILE. A turn that ends is `idle`, never `finished`.
The agent's last message might be "done, opened a PR", might be "which auth
library?", might be a stall - and ACP gives no signal distinguishing them,
because `session/prompt` returns a `stop_reason` and nothing else. Deciding
which it is needs the goal, the thread, the member, and the household, none
of which exist on this box and none of which are going to. So the box
records and Eve classifies. Anything that adds judgement to this file is
the design going wrong.

WHY THE CONNECTION LIVES IN A BACKGROUND TASK. `acp.spawn_agent_process` is
an async context manager that closes the connection on exit, so the session
has to hold that context open for its whole life - which can be hours,
across many HTTP requests. `_drive` is that holder: it owns the context,
consumes prompts off a queue, and only returns when the session ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import acp
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.schema import ClientCapabilities, FileSystemCapabilities, Implementation

from eve_computer.acp import repo
from eve_computer.acp.client import SessionClient
from eve_computer.acp.registry import build
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

# Enough for "what is it doing right now", not a second transcript.
_ACTIVITY_MAX = 20

_SYSTEM_HINT = (
    "You are working in a git worktree on behalf of an assistant named Eve, "
    "who is relaying a request from a family member. Commit your work on the "
    "current branch; do not push and do not open a pull request - that is "
    "handled for you. Never use bare `git stash`: several worktrees share one "
    "stash stack here and you would pop someone else's work. Make a WIP commit "
    "on your own branch instead."
)


@dataclass
class Turn:
    role: str  # "user" | "agent"
    text: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Session:
    id: str
    agent: str
    model: str
    repos: list[str]
    branch: str
    directory: Path
    status: str = "queued"  # queued -> running -> idle -> finished|failed|killed
    turns: list[Turn] = field(default_factory=list)
    activity: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    error: str = ""
    prs: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    prompts: asyncio.Queue = field(default_factory=asyncio.Queue)
    driver: asyncio.Task | None = None
    conn: object | None = None


_SESSIONS: dict[str, Session] = {}
_lock = asyncio.Lock()
_semaphore: asyncio.Semaphore | None = None


def _limiter() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_computer_settings().max_concurrent_sessions)
    return _semaphore


def _record_activity(session: Session, line: str) -> None:
    session.activity.append(line)
    del session.activity[:-_ACTIVITY_MAX]


def _describe(update: object) -> str | None:
    """One human line for the rolling activity window, or None to ignore."""
    kind = getattr(update, "session_update", None)
    if kind == "tool_call":
        return f"tool: {getattr(update, 'title', '')}"
    if kind == "tool_call_update":
        return f"tool: {getattr(update, 'title', '') or ''} ({getattr(update, 'status', '')})"
    if kind == "agent_message_chunk":
        content = getattr(update, "content", None)
        text = getattr(content, "text", None)
        return f"saying: {text[:80]}" if text else None
    return None


def _chunk_text(update: object) -> str | None:
    if getattr(update, "session_update", None) != "agent_message_chunk":
        return None
    return getattr(getattr(update, "content", None), "text", None)


async def _spawn(client: SessionClient, argv: list[str], env: dict[str, str], cwd: Path):
    """Seam. Returns (connection, closer). Faked wholesale in unit tests -
    a real ACP subprocess in a unit test would be an integration test
    wearing the wrong marker."""
    manager = spawn_agent_process(client, argv[0], *argv[1:], env=env, cwd=str(cwd))
    conn, _process = await manager.__aenter__()
    return conn, manager


async def create(
    session_id: str, agent: str, model: str, repos: list[str], prompt: str
) -> Session:
    settings = get_computer_settings()
    # Raises UnknownAgent before anything is created, so a bad agent name
    # never leaves a half-built session or an orphaned worktree behind.
    argv, env = build(agent, model)

    branch = f"eve/{repo.slug(prompt)}-{uuid.uuid4().hex[:8]}"
    directory = Path(settings.sessions_dir) / session_id
    directory.mkdir(parents=True, exist_ok=True)

    session = Session(
        id=session_id, agent=agent, model=model, repos=list(repos),
        branch=branch, directory=directory,
    )
    async with _lock:
        _SESSIONS[session_id] = session

    for name in repos:
        await repo.add_worktree(name, directory, branch)

    await session.prompts.put(prompt)
    session.turns.append(Turn(role="user", text=prompt))
    session.driver = asyncio.create_task(_drive(session, argv, env))
    return session


async def _drive(session: Session, argv: list[str], env: dict[str, str]) -> None:
    settings = get_computer_settings()
    manager = None
    try:
        async with _limiter():
            client = SessionClient(
                root=session.directory,
                on_update=lambda update: _on_update(session, update),
            )
            conn, manager = await _spawn(client, argv, env, session.directory)
            session.conn = conn

            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(
                    fs=FileSystemCapabilities(read_text_file=True, write_text_file=True)
                ),
                client_info=Implementation(name="eve-computer", version="1"),
            )
            created = await conn.new_session(
                cwd=str(session.directory),
                additional_directories=[
                    str(repo.worktree_path(session.directory, name))
                    for name in session.repos
                ],
            )
            acp_session_id = created.session_id
            await conn.prompt(
                session_id=acp_session_id, prompt=[text_block(_SYSTEM_HINT)]
            )

            turns = 0
            while True:
                text = await session.prompts.get()
                if text is None:
                    return
                turns += 1
                if turns > settings.session_max_turns:
                    _fail(session, f"max turns ({settings.session_max_turns}) exceeded")
                    return
                session.status = "running"
                session._chunks = []  # type: ignore[attr-defined]
                response = await asyncio.wait_for(
                    conn.prompt(session_id=acp_session_id, prompt=[text_block(text)]),
                    timeout=settings.session_turn_timeout_seconds,
                )
                reply = "".join(getattr(session, "_chunks", []))
                if reply:
                    session.turns.append(Turn(role="agent", text=reply))
                if response.stop_reason in ("refusal", "max_tokens", "max_turn_requests"):
                    _fail(session, f"agent stopped: {response.stop_reason}")
                    return
                if response.stop_reason == "cancelled":
                    session.status = "killed"
                    return
                session.status = "idle"
    except asyncio.CancelledError:
        session.status = "killed"
        raise
    except TimeoutError:
        _fail(session, f"turn exceeded {get_computer_settings().session_turn_timeout_seconds}s")
    except Exception as exc:
        logger.warning("session %s failed", session.id, exc_info=True)
        _fail(session, f"{exc.__class__.__name__}: {exc}")
    finally:
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.__aexit__(None, None, None)


def _on_update(session: Session, update: object) -> None:
    line = _describe(update)
    if line:
        _record_activity(session, line)
    chunk = _chunk_text(update)
    if chunk:
        session._chunks = [*getattr(session, "_chunks", []), chunk]  # type: ignore[attr-defined]


def _fail(session: Session, message: str) -> None:
    session.status = "failed"
    session.error = message


def get(session_id: str) -> Session | None:
    return _SESSIONS.get(session_id)


async def send(session_id: str, text: str) -> None:
    session = _SESSIONS[session_id]
    session.turns.append(Turn(role="user", text=text))
    session.pending.clear()
    await session.prompts.put(text)


async def enqueue(session_id: str, message: str) -> None:
    """A family member's interjection. Recorded, never delivered by the box:
    Eve composes the next prompt so a correction and the agent's own open
    question are answered together instead of racing."""
    _SESSIONS[session_id].pending.append(message)


async def close(session_id: str) -> dict:
    session = _SESSIONS[session_id]
    session.prs = await repo.publish(session.directory, session.repos, session.branch)
    if session.driver:
        await session.prompts.put(None)
        session.driver.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await session.driver
    with contextlib.suppress(Exception):
        await session.conn.close_session(session_id=session_id)  # type: ignore[union-attr]
    await repo.remove_worktrees(session.directory, session.repos)
    session.status = "finished"
    return {"prs": session.prs}


async def kill(session_id: str) -> None:
    session = _SESSIONS[session_id]
    with contextlib.suppress(Exception):
        await session.conn.cancel(session_id=session_id)  # type: ignore[union-attr]
    if session.driver:
        session.driver.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await session.driver
    session.status = "killed"


def snapshot(session: Session, since: int = 0) -> dict:
    return {
        "status": session.status,
        "agent": session.agent,
        "model": session.model,
        "repos": session.repos,
        "branch": session.branch,
        "activity": list(session.activity),
        "turns": [
            {"role": t.role, "text": t.text, "at": t.at.isoformat()}
            for t in session.turns[since:]
        ],
        "cursor": len(session.turns),
        "pending": list(session.pending),
        "error": session.error,
        "prs": session.prs,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group computer pytest tests/test_acp_session.py -v`
Expected: PASS (11 tests)

If `test_kill_cancels_the_agent_and_keeps_the_worktrees` is flaky, the cause is `_settle()` racing the driver task, not the implementation — increase the loop count in `_settle`, do not add a `sleep` to `kill`.

- [ ] **Step 5: Commit**

```bash
git add src/eve_computer/acp/session.py tests/test_acp_session.py
git commit -m "feat(eve-acp): add the session state machine, which never classifies idle"
```

---
## Task 5: The session HTTP surface

Five routes beside `/tasks`. The GUI queue and its single worker are not touched — coding sessions need no display, so serialising them behind the one mouse would be a bound with nothing behind it.

**Files:**
- Modify: `src/eve_computer/app.py`
- Test: `tests/test_acp_app.py`

**Interfaces:**
- Consumes: everything `session.py` produces (Task 4).
- Produces (HTTP, all bearer-authenticated with `EVE_COMPUTER_API_KEY`):
  - `POST /sessions` `{id, agent, model, repos, prompt}` → `202 {"id", "status"}`
  - `GET /sessions/{id}?since=<int>` → the `snapshot()` dict
  - `POST /sessions/{id}/prompt` `{text, kind}` where `kind` is `"reply" | "interjection"` → `{"status"}`
  - `POST /sessions/{id}/close` → `{"prs": [...]}`
  - `DELETE /sessions/{id}` → `{"id", "status": "killed"}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_acp_app.py`:

```python
"""The door, not the machinery. `session` is mocked wholesale: Task 4 owns
the state machine's behaviour and re-testing it through HTTP would only
make both suites slower to change."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from eve_computer import app as app_mod


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "secret")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    yield
    get_computer_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app_mod.app) as test_client:
        yield test_client


AUTH = {"Authorization": "Bearer secret"}


def test_every_session_route_requires_the_bearer_token(client):
    assert client.post("/sessions", json={}).status_code == 401
    assert client.get("/sessions/s1").status_code == 401
    assert client.post("/sessions/s1/prompt", json={"text": "x"}).status_code == 401
    assert client.post("/sessions/s1/close").status_code == 401
    assert client.delete("/sessions/s1").status_code == 401


def test_creating_a_session_returns_202(client, monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(app_mod.session, "create", create)

    response = client.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "chatgpt/gpt-5.6-sol",
              "repos": ["acme/repo"], "prompt": "fix it"},
        headers=AUTH,
    )

    assert response.status_code == 202
    assert response.json() == {"id": "s1", "status": "queued"}
    create.assert_awaited_once_with("s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "fix it")


def test_an_unknown_agent_is_a_400_not_a_500(client, monkeypatch):
    from eve_computer.acp.registry import UnknownAgent

    monkeypatch.setattr(app_mod.session, "create", AsyncMock(side_effect=UnknownAgent("nope")))

    response = client.post(
        "/sessions",
        json={"id": "s1", "agent": "cursor", "model": "m", "repos": ["r"], "prompt": "p"},
        headers=AUTH,
    )

    assert response.status_code == 400


def test_getting_a_session_passes_the_cursor_through(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    monkeypatch.setattr(
        app_mod.session, "snapshot",
        lambda s, since: {"status": "idle", "turns": [], "cursor": since},
    )

    response = client.get("/sessions/s1?since=4", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["cursor"] == 4


def test_an_unknown_session_is_404_everywhere(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: None)

    assert client.get("/sessions/nope", headers=AUTH).status_code == 404
    assert client.post("/sessions/nope/prompt", json={"text": "x"}, headers=AUTH).status_code == 404
    assert client.post("/sessions/nope/close", headers=AUTH).status_code == 404
    assert client.delete("/sessions/nope", headers=AUTH).status_code == 404


def test_a_reply_is_sent_and_an_interjection_is_only_queued(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    send, enqueue = AsyncMock(), AsyncMock()
    monkeypatch.setattr(app_mod.session, "send", send)
    monkeypatch.setattr(app_mod.session, "enqueue", enqueue)

    client.post("/sessions/s1/prompt", json={"text": "go on", "kind": "reply"}, headers=AUTH)
    client.post(
        "/sessions/s1/prompt",
        json={"text": "use httpx", "kind": "interjection"},
        headers=AUTH,
    )

    send.assert_awaited_once_with("s1", "go on")
    enqueue.assert_awaited_once_with("s1", "use httpx")


def test_the_prompt_kind_defaults_to_reply(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    send = AsyncMock()
    monkeypatch.setattr(app_mod.session, "send", send)
    monkeypatch.setattr(app_mod.session, "enqueue", AsyncMock())

    client.post("/sessions/s1/prompt", json={"text": "go on"}, headers=AUTH)

    send.assert_awaited_once_with("s1", "go on")


def test_closing_returns_the_pull_requests(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    monkeypatch.setattr(
        app_mod.session, "close",
        AsyncMock(return_value={"prs": [{"repo": "acme/repo", "commits": 2, "pr_url": "u"}]}),
    )

    response = client.post("/sessions/s1/close", headers=AUTH)

    assert response.json()["prs"][0]["pr_url"] == "u"


def test_deleting_kills_the_session(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    kill = AsyncMock()
    monkeypatch.setattr(app_mod.session, "kill", kill)

    response = client.delete("/sessions/s1", headers=AUTH)

    assert response.json() == {"id": "s1", "status": "killed"}
    kill.assert_awaited_once_with("s1")


def test_the_gui_task_queue_is_untouched(client, monkeypatch):
    """The GUI lane's single worker is the whole reason it exists. A change
    that lets two GUI tasks run at once would be silent and would fight over
    one mouse."""
    from unittest.mock import AsyncMock as AM

    monkeypatch.setattr(app_mod.store, "create", AM())
    response = client.post("/tasks", json={"id": "t1", "goal": "click"}, headers=AUTH)
    assert response.status_code == 202
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group computer pytest tests/test_acp_app.py -v`
Expected: FAIL — `AttributeError: module 'eve_computer.app' has no attribute 'session'`

- [ ] **Step 3: Add the routes**

Edit `src/eve_computer/app.py`. Add to the imports, after `from eve_computer import store`:

```python
from eve_computer.acp import session
from eve_computer.acp.registry import UnknownAgent
```

Then append to the end of the file:

```python
# --- Sessions (EVE-4) -------------------------------------------------
#
# A second lane, not a second queue. `/tasks` is serialised because one
# machine has one X display and one mouse; a coding session needs neither,
# so its only bound is `max_concurrent_sessions` inside session.py. Sharing
# the GUI worker would make a half-hour conversation block a screenshot.


class SessionRequest(BaseModel):
    id: str
    agent: str
    model: str
    repos: list[str]
    prompt: str


class PromptRequest(BaseModel):
    text: str
    # "reply" is Eve's own composed next prompt; "interjection" is a family
    # member's correction, which the box records and never delivers itself.
    kind: str = "reply"


def _require_session(session_id: str):
    found = session.get(session_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return found


@app.post("/sessions", status_code=202)
async def create_session_route(
    body: SessionRequest, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    try:
        await session.create(body.id, body.agent, body.model, body.repos, body.prompt)
    except UnknownAgent as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": body.id, "status": "queued"}


@app.get("/sessions/{session_id}")
async def get_session_route(
    session_id: str, since: int = 0, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    return session.snapshot(_require_session(session_id), since=since)


@app.post("/sessions/{session_id}/prompt")
async def prompt_session_route(
    session_id: str,
    body: PromptRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    _check_auth(authorization)
    _require_session(session_id)
    if body.kind == "interjection":
        await session.enqueue(session_id, body.text)
        return {"status": "queued"}
    await session.send(session_id, body.text)
    return {"status": "sent"}


@app.post("/sessions/{session_id}/close")
async def close_session_route(
    session_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    _require_session(session_id)
    return await session.close(session_id)


@app.delete("/sessions/{session_id}")
async def delete_session_route(
    session_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    _require_session(session_id)
    await session.kill(session_id)
    return {"id": session_id, "status": "killed"}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group computer pytest tests/test_acp_app.py tests/test_computer_app.py -v`
Expected: PASS — including the existing `/tasks` tests, unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/eve_computer/app.py tests/test_acp_app.py
git commit -m "feat(eve-acp): add the session routes as a second lane beside /tasks"
```

---

## Task 6: The image and the routing config

Four binaries and three config files. `$HOME` is the PVC, so the config files cannot live in the image — they are written on every start, which is also what makes a wiped PVC self-heal.

**Files:**
- Modify: `Dockerfile.eve-computer`
- Modify: `src/eve_computer/bootstrap.sh`
- Create: `src/eve_computer/templates/codex-config.toml`
- Create: `src/eve_computer/templates/opencode.json`
- Test: `tests/test_computer_docker_image.py` (extend)

**Interfaces:**
- Consumes: `EVE_COMPUTER_LITELLM_BASE_URL`, `EVE_COMPUTER_LITELLM_API_KEY` from the environment.
- Produces: `codex-acp`, `claude-code-acp`, `opencode`, `gh` on `PATH`; `~/.codex/config.toml`, `~/.config/opencode/opencode.json`, and `LITELLM_API_KEY` exported for the agent subprocesses.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_computer_docker_image.py`:

```python
@pytest.mark.docker
def test_the_image_carries_every_coding_binary(image):
    """EVE-4 adds three ACP agents and `gh`. A missing one fails at the
    first session, half an hour after Eve told a member she was on it."""
    for binary in ("gh", "codex-acp", "claude-code-acp", "opencode"):
        _run_in_image(image, ["sh", "-c", f"command -v {binary}"])


@pytest.mark.docker
def test_bootstrap_writes_all_three_routing_configs(image):
    """A wiped PVC must recover model routing with no human involved
    (design doc: "Storage"). $HOME is the PVC, so these cannot be baked in."""
    script = (
        "EVE_COMPUTER_LITELLM_BASE_URL=https://litellm.example "
        "EVE_COMPUTER_LITELLM_API_KEY=sk-probe "
        "/app/src/eve_computer/bootstrap.sh >/dev/null 2>&1; "
        "cat /home/eve/.codex/config.toml; "
        "cat /home/eve/.config/opencode/opencode.json"
    )
    output = _run_in_image(image, ["sh", "-c", script])

    assert "https://litellm.example" in output
    assert 'wire_api = "responses"' in output
    assert "LITELLM_API_KEY" in output
    # The key itself is never written into a config file - only the name of
    # the environment variable holding it.
    assert "sk-probe" not in output
```

Match the existing helper names in that file — if the current tests call the container differently than `_run_in_image(image, argv)`, adapt these two tests to the helper that is actually there rather than adding a second one.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_computer_docker_image.py -m docker -v`
Expected: FAIL on the missing binaries (or SKIP if Docker is unavailable — in that case verify by building manually before moving on).

- [ ] **Step 3: Write the config templates**

Create `src/eve_computer/templates/codex-config.toml`:

```toml
# Written to ~/.codex/config.toml by bootstrap.sh on every container start.
# $HOME is the PVC, so this cannot be baked into the image - and writing it
# every start is what lets a wiped PVC recover routing with no human.
#
# No `model` key: the model is chosen per session by Eve and arrives as
# `codex-acp --model <name>` (spec: "Agent and model are both chosen per
# task"). Pinning one here would silently override her choice.
#
# `env_key` names the variable holding the key; the key itself is never
# written into this file. `experimental_bearer_token` would inline it and is
# deliberately not used.
model_provider = "litellm"

[model_providers.litellm]
name = "LiteLLM"
base_url = "__LITELLM_BASE_URL__/v1"
env_key = "LITELLM_API_KEY"
wire_api = "responses"
```

Create `src/eve_computer/templates/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "litellm": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "__LITELLM_BASE_URL__/v1",
        "apiKey": "{env:LITELLM_API_KEY}"
      },
      "models": {}
    }
  }
}
```

`"models": {}` is deliberate: OpenCode accepts a model it has not been told about ahead of time, and enumerating them here would be the static catalogue the spec argues against.

- [ ] **Step 4: Write the bootstrap additions**

Edit `src/eve_computer/bootstrap.sh`. Change the `mkdir` line from:

```sh
mkdir -p /home/eve/.eve /home/eve/tasks
```

to:

```sh
mkdir -p /home/eve/.eve /home/eve/tasks /home/eve/sessions /home/eve/code \
         /home/eve/.codex /home/eve/.config/opencode
```

Then insert this block immediately before the `Xvfb` line:

```sh
# Model routing for the three ACP coding agents (EVE-4). Rewritten on every
# start rather than baked into the image: $HOME is the PVC, so a wiped or
# fresh volume would otherwise come up with no routing at all and every
# session would fail at its first prompt.
#
# Claude Code needs no file - it reads ANTHROPIC_BASE_URL from the
# environment the registry hands its subprocess.
LITELLM_BASE_URL="${EVE_COMPUTER_LITELLM_BASE_URL:-https://litellm.chalifour.dev}"
for template in codex-config.toml:/home/eve/.codex/config.toml \
                opencode.json:/home/eve/.config/opencode/opencode.json; do
    src="/app/src/eve_computer/templates/${template%%:*}"
    dest="${template#*:}"
    sed "s|__LITELLM_BASE_URL__|${LITELLM_BASE_URL}|g" "$src" > "$dest"
done

# The agents read the key by name (`env_key` / `{env:...}`), so it is
# exported here and never written into a config file on the PVC.
export LITELLM_API_KEY="${EVE_COMPUTER_LITELLM_API_KEY:-}"
```

- [ ] **Step 5: Extend the Dockerfile**

Edit `Dockerfile.eve-computer`. Change the `RUN apt-get` block's `npm install` line from:

```dockerfile
    && npm install -g @openai/codex \
```

to:

```dockerfile
    && npm install -g @openai/codex @agentclientprotocol/claude-code-acp opencode-ai \
    && curl -fsSL https://raw.githubusercontent.com/agentclientprotocol/codex-acp/main/install.sh | sh \
```

and add `gh` to the `apt-get install` list. If `gh` is not in Debian slim's default repositories, add GitHub's apt source in the same `RUN`; verify with `docker run --rm <image> gh --version` before committing.

Then add the templates to the copied source — change:

```dockerfile
COPY src/eve_computer ./src/eve_computer
```

That line already copies `templates/` because it copies the whole package. Confirm rather than assume: `docker run --rm <image> ls /app/src/eve_computer/templates`.

Add the session directories to the `chown`:

```dockerfile
RUN useradd --system --uid 10004 --create-home --shell /bin/bash eve \
    && mkdir -p /home/eve/sessions /home/eve/code \
    && chown -R eve:eve /app /home/eve \
```

- [ ] **Step 6: Run the docker tests**

Run: `uv run pytest tests/test_computer_docker_image.py -m docker -v`
Expected: PASS. A cold build installing Chromium, Node, and four CLIs takes several minutes — this matches the file's existing documented cost.

- [ ] **Step 7: Record the one-time human step**

`gh` is installed but not authenticated, and nothing in this plan can
authenticate it. Add to `docs/architecture.md`'s `eve-computer` section (or
wherever that file documents the VNC provisioning of her other accounts):

> **Eve's GitHub login is provisioned by hand, once, over VNC.**
> `kubectl port-forward` to the VNC port, open a terminal on her desktop,
> run `gh auth login`, and complete the browser flow as Eve's own GitHub
> account. The token lands in `/home/eve/.config/gh/hosts.yml` on the PVC -
> never a Kubernetes Secret, never an environment variable, never a line in
> this repository, exactly like her browser session cookies.
>
> Which repositories she can touch is her collaborator status on GitHub.
> There is no allow-list in this repo to keep in sync, and revocation is a
> checkbox in your account (ADR 0015).

Until this is done every session will fail at `git push`. That failure is
loud and lands in the pull-request result Eve reports, which is the intended
behaviour — but it is worth doing before the first real delegation rather
than discovering it thirty minutes in.

- [ ] **Step 8: Commit**

```bash
git add Dockerfile.eve-computer src/eve_computer/bootstrap.sh \
        src/eve_computer/templates/ tests/test_computer_docker_image.py \
        docs/architecture.md
git commit -m "feat(eve-acp): install the three agents and write their routing config on boot"
```

---

## Task 7: Eve's session table

Eve's own record, mirroring `eve_computer_task`. The box keeps its sessions in memory and loses them on restart; this is the side that persists.

**Files:**
- Create: `alembic/versions/0005_eve_coding_session.py`
- Create: `src/eve/coding/__init__.py`
- Create: `src/eve/coding/store.py`
- Test: `tests/test_coding_store.py`

**Interfaces:**
- Consumes: `eve.memory.db.get_pool`.
- Produces:
  - `async create_session(session_id, member_sub, thread_id, goal, agent, model, repos, context) -> None`
  - `async get(session_id) -> dict | None`
  - `async live_sessions() -> list[dict]` — status in `('running', 'idle')`
  - `async live_sessions_for(member_sub) -> list[dict]`
  - `async set_status(session_id, status) -> None`
  - `async advance_cursor(session_id, cursor) -> None`
  - `async bump_supervisor_turns(session_id) -> int`
  - `async mark_resolved(session_id, status, result) -> None`
  - `async recently_resolved_sessions(since: datetime) -> list[dict]`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/0005_eve_coding_session.py`:

```python
"""Eve's own record of a delegated coding session.

Revision ID: 0005_eve_coding_session
Revises: 0004_eve_computer_task

The box keeps its sessions in memory and loses them on restart, exactly as
it does for GUI tasks; this table is Eve's side of that boundary. Two
columns exist that `eve_computer_task` has no equivalent for:

`cursor` is how much of the box's turn log Eve has already read. It is
Eve's bookmark, not the box's - a restart on either side must not replay a
conversation she has already reasoned over.

`context` is the recall snapshot taken once, at creation. The supervisor
runs every ~20s and a hybrid recall per tick would be indefensible; taking
it once is what makes running the supervisor in Eve's container - the whole
reason it lives there - affordable.
"""
from alembic import op

revision = "0005_eve_coding_session"
down_revision = "0004_eve_computer_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_coding_session (
          id               text        PRIMARY KEY,
          member_sub       text        NOT NULL,
          thread_id        text        NOT NULL,
          goal             text        NOT NULL,
          agent            text        NOT NULL,
          model            text        NOT NULL,
          repos            jsonb       NOT NULL,
          context          text        NOT NULL DEFAULT '',
          status           text        NOT NULL DEFAULT 'running',
          cursor           integer     NOT NULL DEFAULT 0,
          supervisor_turns integer     NOT NULL DEFAULT 0,
          result           jsonb,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          finished_at      timestamptz
        )
        """
    )
    # The supervisor's own query, every tick: "every session still live."
    op.execute(
        "CREATE INDEX eve_coding_session_status"
        " ON eve_coding_session (status, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_coding_session")
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_coding_store.py`, following the connection-mocking shape already used by `tests/test_computer_store.py` — open that file first and mirror its fixture rather than inventing a second pattern:

```python
"""Statement-level tests: the store's job is to emit the right SQL with the
right parameters, and a real Postgres for that belongs in the integration
tier (tests/test_memory_integration.py's shape), not here."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from eve.coding import store


@pytest.fixture
def conn(monkeypatch):
    connection = MagicMock()
    connection.execute = AsyncMock()
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    connection.cursor.return_value.__aenter__ = AsyncMock(return_value=cursor)
    connection.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(store, "get_pool", AsyncMock(return_value=pool))
    connection.cursor_obj = cursor
    return connection


async def test_create_session_inserts_every_column_the_supervisor_needs(conn):
    await store.create_session(
        session_id="s1", member_sub="sub-noah", thread_id="t1", goal="fix it",
        agent="codex", model="chatgpt/gpt-5.6-sol", repos=["acme/repo"],
        context="Noah prefers httpx.",
    )

    sql, params = conn.execute.await_args.args
    assert "INSERT INTO eve_coding_session" in sql
    assert "sub-noah" in params and "codex" in params
    assert "Noah prefers httpx." in params


async def test_live_sessions_asks_for_running_and_idle(conn):
    await store.live_sessions()

    sql = conn.cursor_obj.execute.await_args.args[0]
    assert "status IN ('running', 'idle')" in sql


async def test_live_sessions_for_a_member_is_scoped_to_that_member(conn):
    await store.live_sessions_for("sub-noah")

    sql, params = conn.cursor_obj.execute.await_args.args
    assert "member_sub = %s" in sql
    assert params == ("sub-noah",)


async def test_advance_cursor_records_the_bookmark(conn):
    await store.advance_cursor("s1", 7)

    sql, params = conn.execute.await_args.args
    assert "cursor = %s" in sql
    assert params == (7, "s1")


async def test_bump_supervisor_turns_returns_the_new_count(conn):
    conn.cursor_obj.fetchone = AsyncMock(return_value={"supervisor_turns": 4})

    assert await store.bump_supervisor_turns("s1") == 4

    sql = conn.cursor_obj.execute.await_args.args[0]
    assert "supervisor_turns = supervisor_turns + 1" in sql
    assert "RETURNING" in sql


async def test_mark_resolved_stamps_finished_at(conn):
    await store.mark_resolved("s1", "finished", {"prs": []})

    sql, params = conn.execute.await_args.args
    assert "finished_at = now()" in sql
    assert params[0] == "finished"


async def test_recently_resolved_covers_every_terminal_status(conn):
    from datetime import UTC, datetime

    await store.recently_resolved_sessions(since=datetime.now(UTC))

    sql = conn.cursor_obj.execute.await_args.args[0]
    for status in ("finished", "failed", "stale", "blocked"):
        assert status in sql
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_coding_store.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve.coding'`

- [ ] **Step 4: Write the implementation**

Create `src/eve/coding/__init__.py` (empty), then `src/eve/coding/store.py`:

```python
"""Every eve_coding_session SQL statement. Eve's own record of a delegated
coding session - not the box's live session, which the box holds in memory
and loses on restart.

`status` here is Eve's vocabulary, not the box's, and the two deliberately
differ. The box has `idle`; Eve has `blocked`, which the box could never
produce because deciding that an agent's question is unanswerable needs the
member and the household. Terminal for Eve: finished, failed, stale,
blocked.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


async def create_session(
    session_id: str,
    member_sub: str,
    thread_id: str,
    goal: str,
    agent: str,
    model: str,
    repos: list[str],
    context: str,
) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_coding_session"
            " (id, member_sub, thread_id, goal, agent, model, repos, context, status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running')",
            (session_id, member_sub, thread_id, goal, agent, model, Jsonb(repos), context),
        )


async def get(session_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM eve_coding_session WHERE id = %s", (session_id,))
            return await cur.fetchone()


async def live_sessions() -> list[dict]:
    """Every session the supervisor is still driving."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_coding_session"
                " WHERE status IN ('running', 'idle', 'blocked')"
                " ORDER BY created_at"
            )
            return list(await cur.fetchall())


async def live_sessions_for(member_sub: str) -> list[dict]:
    """What `check_coding_session` lists. Scoped to the asking member: one
    member has no business seeing another's delegated work in a tool result."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_coding_session"
                " WHERE status IN ('running', 'idle', 'blocked') AND member_sub = %s"
                " ORDER BY created_at",
                (member_sub,),
            )
            return list(await cur.fetchall())


async def set_status(session_id: str, status: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_coding_session SET status = %s, updated_at = now() WHERE id = %s",
            (status, session_id),
        )


async def advance_cursor(session_id: str, cursor: int) -> None:
    """Eve's bookmark into the box's turn log. Advanced only after a turn has
    actually been reasoned over, so a crash mid-decision re-reads rather than
    skips."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_coding_session SET cursor = %s, updated_at = now() WHERE id = %s",
            (cursor, session_id),
        )


async def bump_supervisor_turns(session_id: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE eve_coding_session"
                " SET supervisor_turns = supervisor_turns + 1, updated_at = now()"
                " WHERE id = %s RETURNING supervisor_turns",
                (session_id,),
            )
            row = await cur.fetchone()
            return row["supervisor_turns"] if row else 0


async def mark_resolved(session_id: str, status: str, result: dict) -> None:
    """`status` is one of finished, failed, stale, blocked."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_coding_session SET status = %s, result = %s,"
            " updated_at = now(), finished_at = now() WHERE id = %s",
            (status, Jsonb(result), session_id),
        )


async def recently_resolved_sessions(since: datetime) -> list[dict]:
    """Every session that resolved since `since` - not only those that
    resolved on this exact tick. Lets the ambient source re-derive a signal
    whose delivery was suppressed (quiet hours, daily cap) or deferred, the
    same way every other polled source re-derives from live upstream state."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_coding_session"
                " WHERE status IN ('finished', 'failed', 'stale', 'blocked')"
                "   AND finished_at >= %s"
                " ORDER BY finished_at",
                (since,),
            )
            return list(await cur.fetchall())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_coding_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Verify the migration applies**

Run: `docker compose -f docker-compose.test.yml up -d && uv run eve-migrate`
Expected: `0005_eve_coding_session` applies cleanly. Then `uv run pytest -m integration -q` to confirm nothing else regressed.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/0005_eve_coding_session.py src/eve/coding/ tests/test_coding_store.py
git commit -m "feat(eve-acp): add Eve's coding session table and store"
```

---
## Task 8: The session doors in `tools_client`

Sessions live on `eve-computer`, so there is no new base URL — `computer_base_url` and `computer_api_key` already point at the right box.

**Files:**
- Modify: `src/eve/tools_client.py`
- Modify: `src/eve/settings.py`
- Test: `tests/test_tools_client.py` (extend)

**Interfaces:**
- Consumes: Task 5's HTTP surface.
- Produces:
  - `async create_coding_session(session_id, agent, model, repos, prompt) -> str` — `"ok"` or `"error: …"`
  - `async get_coding_session(session_id, since=0) -> dict | None` — `None` on any failure
  - `async prompt_coding_session(session_id, text, kind="reply") -> str`
  - `async close_coding_session(session_id) -> dict | None`
  - `async kill_coding_session(session_id) -> str`
- New settings: `coding_enabled`, `coding_default_agent`, `coding_supervisor_interval_seconds`, `coding_session_stale_minutes`, `coding_max_supervisor_turns`, `coding_catalogue_ttl_seconds`.

- [ ] **Step 1: Add the settings**

Edit `src/eve/settings.py`. After the Phase 6 block (ending `computer_task_stale_minutes: int = 120`), add:

```python
    # EVE-4 (ACP tools). See docs/superpowers/specs/
    # 2026-09-01-eve-acp-tools-design.md.
    #
    # Off by default for the same reason ambient_enabled and computer_enabled
    # are: this one opens pull requests under Eve's own GitHub account, and a
    # deployment that has not deliberately enabled it must open none.
    #
    # No base URL of its own: sessions run on eve-computer, so
    # computer_base_url and computer_api_key already point at the right box.
    coding_enabled: bool = False
    # The tiebreak when neither the task nor the member's preferences point
    # anywhere. Codex rides the ChatGPT subscription, so the default case
    # costs nothing metered (spec: "Codex breaks ties").
    coding_default_agent: str = "codex"
    # Deliberately not ambient_poll_interval_seconds. The supervisor is a
    # control loop with an agent waiting on the other end, not a notification
    # pipeline; 300s of latency per conversational turn would make Eve a
    # worse correspondent than the member she is standing in for.
    coding_supervisor_interval_seconds: int = 20
    coding_session_stale_minutes: int = 120
    # Whatever the budget is, a loop that blows it has to answer in English
    # (graph.py's _LOOP_EXHAUSTED). Hitting this parks the session and asks
    # the member rather than stalling silently.
    coding_max_supervisor_turns: int = 30
    coding_catalogue_ttl_seconds: int = 3600
    # The outermost bound. The box enforces per-turn and per-session limits
    # of its own, but a session parked on `blocked` is not running anything
    # for the box to time out - it is waiting on a human who may never
    # answer, holding a subprocess, a worktree, and a concurrency slot.
    coding_session_timeout_seconds: int = 28800
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_tools_client.py`, matching the `respx` mocking already used there:

```python
import respx
from httpx import Response

from eve import tools_client


@respx.mock
async def test_create_coding_session_posts_the_agent_and_model(monkeypatch):
    route = respx.post("http://eve-computer:8092/sessions").mock(
        return_value=Response(202, json={"id": "s1", "status": "queued"})
    )

    result = await tools_client.create_coding_session(
        "s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "fix it"
    )

    assert result == "ok"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "id": "s1", "agent": "codex", "model": "chatgpt/gpt-5.6-sol",
        "repos": ["acme/repo"], "prompt": "fix it",
    }


@respx.mock
async def test_create_coding_session_degrades_to_an_error_string():
    respx.post("http://eve-computer:8092/sessions").mock(return_value=Response(500))

    result = await tools_client.create_coding_session("s1", "codex", "m", ["r"], "p")

    assert result.startswith("error:")


@respx.mock
async def test_get_coding_session_passes_the_cursor():
    route = respx.get("http://eve-computer:8092/sessions/s1").mock(
        return_value=Response(200, json={"status": "idle", "turns": [], "cursor": 3})
    )

    result = await tools_client.get_coding_session("s1", since=3)

    assert result["cursor"] == 3
    assert route.calls.last.request.url.params["since"] == "3"


@respx.mock
async def test_get_coding_session_returns_none_when_the_box_is_unreachable():
    respx.get("http://eve-computer:8092/sessions/s1").mock(return_value=Response(503))

    assert await tools_client.get_coding_session("s1") is None


@respx.mock
async def test_prompt_carries_its_kind():
    route = respx.post("http://eve-computer:8092/sessions/s1/prompt").mock(
        return_value=Response(200, json={"status": "queued"})
    )

    await tools_client.prompt_coding_session("s1", "use httpx", kind="interjection")

    assert json.loads(route.calls.last.request.content)["kind"] == "interjection"


@respx.mock
async def test_close_returns_the_pull_requests():
    respx.post("http://eve-computer:8092/sessions/s1/close").mock(
        return_value=Response(200, json={"prs": [{"repo": "acme/repo", "pr_url": "u"}]})
    )

    result = await tools_client.close_coding_session("s1")

    assert result["prs"][0]["pr_url"] == "u"


@respx.mock
async def test_every_session_call_sends_the_computer_bearer_token(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "secret")
    from eve.settings import get_settings

    get_settings.cache_clear()
    route = respx.post("http://eve-computer:8092/sessions").mock(
        return_value=Response(202, json={})
    )

    await tools_client.create_coding_session("s1", "codex", "m", ["r"], "p")

    assert route.calls.last.request.headers["authorization"] == "Bearer secret"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools_client.py -v`
Expected: FAIL, `AttributeError: module 'eve.tools_client' has no attribute 'create_coding_session'`

- [ ] **Step 4: Write the implementation**

Append to `src/eve/tools_client.py`:

```python
# --- Coding sessions (EVE-4) ------------------------------------------
#
# Sessions run on eve-computer, so these reuse computer_base_url and
# computer_api_key rather than introducing a fourth door to the same box.
#
# Same failure posture as everything else in this module: a returned error
# string or None, never a raised exception. The callers are a tool whose
# result goes to a model and a supervisor loop that must not die because
# one session's box hiccuped.


async def _session_request(
    method: str, path: str, *, json_body: dict | None = None,
    params: dict | None = None, timeout: float = 15.0,
) -> dict | None:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                f"{settings.computer_base_url}{path}",
                json=json_body,
                params=params,
                headers={"Authorization": f"Bearer {settings.computer_api_key}"},
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        logger.warning("eve-computer session call %s %s failed", method, path, exc_info=True)
        return None


async def create_coding_session(
    session_id: str, agent: str, model: str, repos: list[str], prompt: str
) -> str:
    body = await _session_request(
        "POST", "/sessions",
        json_body={"id": session_id, "agent": agent, "model": model,
                   "repos": repos, "prompt": prompt},
    )
    return "ok" if body is not None else "error: eve-computer unavailable"


async def get_coding_session(session_id: str, since: int = 0) -> dict | None:
    return await _session_request(
        "GET", f"/sessions/{session_id}", params={"since": since}
    )


async def prompt_coding_session(session_id: str, text: str, kind: str = "reply") -> str:
    body = await _session_request(
        "POST", f"/sessions/{session_id}/prompt",
        json_body={"text": text, "kind": kind},
    )
    return "ok" if body is not None else "error: eve-computer unavailable"


async def close_coding_session(session_id: str) -> dict | None:
    # Longer than the default: closing pushes branches and opens a pull
    # request per repo, which is several network round trips to GitHub.
    return await _session_request("POST", f"/sessions/{session_id}/close", timeout=120.0)


async def kill_coding_session(session_id: str) -> str:
    body = await _session_request("DELETE", f"/sessions/{session_id}")
    return "ok" if body is not None else "error: eve-computer unavailable"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools_client.py tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/tools_client.py src/eve/settings.py tests/test_tools_client.py
git commit -m "feat(eve-acp): add the coding session doors to tools_client"
```

---

## Task 9: The model catalogue

LiteLLM's live catalogue, cached, minus the one class of model that fails silently.

**Files:**
- Create: `src/eve/coding/catalogue.py`
- Test: `tests/test_coding_catalogue.py`

**Interfaces:**
- Consumes: `eve.settings` (`litellm_base_url`, `litellm_api_key`, `coding_catalogue_ttl_seconds`).
- Produces:
  - `async available_models() -> list[str]` — cached, deny-list applied, `[]` on failure.
  - `async validate(model: str | None, agent: str) -> str` — returns the model to use, falling back rather than raising.
  - `DENIED_PREFIXES: tuple[str, ...]` — `("ocp/",)`
  - `_reset_cache() -> None` — for tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coding_catalogue.py`:

```python
"""The deny-list is one prefix and it is not a survey. ADR 0004 probed
ocp/* live: the proxy strips tool definitions, so the agent answers
fluently and changes nothing - undetectable at runtime, therefore denied
here. Models the ChatGPT sign-in refuses fail loudly at the first prompt
and need no entry; enumerating them would be a second list to keep in sync
with OpenAI's generation renames."""

import respx
from httpx import Response

import pytest

from eve.coding import catalogue

CATALOGUE = {
    "data": [
        {"id": "chatgpt/gpt-5.6-sol"},
        {"id": "chatgpt/gpt-5.6-luna"},
        {"id": "anthropic/claude-sonnet-5"},
        {"id": "ocp/claude-sonnet-5"},
        {"id": "gemini/gemini-flash-lite-latest"},
    ]
}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("EVE_LITELLM_BASE_URL", "https://litellm.example")
    from eve.settings import get_settings

    get_settings.cache_clear()
    catalogue._reset_cache()
    yield
    get_settings.cache_clear()
    catalogue._reset_cache()


@respx.mock
async def test_the_catalogue_comes_from_litellm():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    models = await catalogue.available_models()

    assert "chatgpt/gpt-5.6-sol" in models
    assert "anthropic/claude-sonnet-5" in models


@respx.mock
async def test_ocp_models_are_denied():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert "ocp/claude-sonnet-5" not in await catalogue.available_models()


@respx.mock
async def test_the_catalogue_is_cached_within_its_ttl():
    route = respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    await catalogue.available_models()
    await catalogue.available_models()

    assert route.call_count == 1


@respx.mock
async def test_an_unreachable_proxy_yields_an_empty_catalogue_not_an_exception():
    respx.get("https://litellm.example/v1/models").mock(return_value=Response(503))

    assert await catalogue.available_models() == []


@respx.mock
async def test_validate_accepts_a_model_in_the_catalogue():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate("chatgpt/gpt-5.6-luna", "codex") == "chatgpt/gpt-5.6-luna"


@respx.mock
async def test_validate_falls_back_for_a_hallucinated_name():
    """A bad name would otherwise kill the session at its first prompt,
    several minutes after Eve promised the member she was on it."""
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate("gpt-9-ultra", "codex") == "chatgpt/gpt-5.6-sol"


@respx.mock
async def test_validate_falls_back_for_a_denied_model():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate("ocp/claude-sonnet-5", "claude") == "anthropic/claude-sonnet-5"


@respx.mock
async def test_validate_falls_back_when_no_model_was_chosen():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate(None, "claude") == "anthropic/claude-sonnet-5"


@respx.mock
async def test_validate_still_answers_when_the_catalogue_is_empty():
    """A proxy outage must not make delegation impossible - the agent's own
    default is a better answer than refusing to start."""
    respx.get("https://litellm.example/v1/models").mock(return_value=Response(503))

    assert await catalogue.validate("chatgpt/gpt-5.6-sol", "codex") == "chatgpt/gpt-5.6-sol"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_coding_catalogue.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve.coding.catalogue'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/coding/catalogue.py`:

```python
"""Which models a delegated coding session may use.

NOT models.py, and deliberately. That file declares itself "the ONLY place
model identifiers appear", and the invariant exists so retiering EVE is a
one-file change - it is about her five tiers. A delegated coding model is
not a tier: it is an argument to a subprocess, chosen per task, drawn from
a set this repository does not define. A static table here would be a
second list to keep in sync with the proxy, going stale silently.

THE DENY-LIST IS ONE PREFIX, ON A PRINCIPLE. The line is not "which models
work" but HOW THEY FAIL. ADR 0004 probed ocp/* live and found the proxy
strips tool definitions before the model sees them: asked to call a tool,
Claude replies it has no such tool. A coding agent that cannot call tools
answers fluently and changes nothing, for half an hour - undetectable at
runtime, so it is denied here. Models the ChatGPT sign-in refuses fail
loudly at the first prompt with the backend saying why; Eve reports it,
retries, and remembers. Loud failures need no registry.
"""

from __future__ import annotations

import logging
import time

import httpx

from eve.settings import get_settings

logger = logging.getLogger(__name__)

DENIED_PREFIXES: tuple[str, ...] = ("ocp/",)

# The fallback when Eve names nothing usable. Not a catalogue - one name per
# agent, which is what "the agent's own sensible default" costs.
_AGENT_FALLBACK: dict[str, str] = {
    "codex": "chatgpt/gpt-5.6-sol",
    "opencode": "chatgpt/gpt-5.6-sol",
    "claude": "anthropic/claude-sonnet-5",
}

_cache: dict[str, object] = {"models": [], "at": 0.0}


def _reset_cache() -> None:
    _cache["models"] = []
    _cache["at"] = 0.0


async def available_models() -> list[str]:
    settings = get_settings()
    now = time.monotonic()
    if _cache["models"] and now - float(_cache["at"]) < settings.coding_catalogue_ttl_seconds:
        return list(_cache["models"])  # type: ignore[arg-type]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.litellm_base_url}/v1/models",
                headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
            )
            response.raise_for_status()
            body = response.json()
    except Exception:
        logger.warning("could not fetch the LiteLLM catalogue", exc_info=True)
        return []

    models = [
        entry["id"]
        for entry in body.get("data", [])
        if entry.get("id") and not entry["id"].startswith(DENIED_PREFIXES)
    ]
    _cache["models"] = models
    _cache["at"] = now
    return list(models)


async def validate(model: str | None, agent: str) -> str:
    """The model to actually use. Falls back rather than raising: Eve has
    already told the member she is on it, and refusing to start over a
    hallucinated name would spend her credibility on our validation."""
    fallback = _AGENT_FALLBACK.get(agent, _AGENT_FALLBACK["codex"])
    if not model:
        return fallback
    if model.startswith(DENIED_PREFIXES):
        logger.info("model %r is denied; falling back to %r", model, fallback)
        return fallback
    models = await available_models()
    if not models:
        # The proxy is unreachable. Eve's choice is at least as good as ours
        # and a delegation that cannot start is worse than one that might.
        return model
    if model not in models:
        logger.info("model %r is not in the catalogue; falling back to %r", model, fallback)
        return fallback
    return model
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_coding_catalogue.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/eve/coding/catalogue.py tests/test_coding_catalogue.py
git commit -m "feat(eve-acp): take model candidates from LiteLLM, denying only silent failures"
```

---

## Task 10: The three tools

Eve's surface. Permission is checked here, in her container, before any HTTP call reaches the box (ADR 0006).

**Files:**
- Create: `src/eve/coding/dispatch.py`
- Test: `tests/test_coding_dispatch.py`

**Interfaces:**
- Consumes: `catalogue.validate` (Task 9), `store.*` (Task 7), `tools_client.*` (Task 8), `eve.memory.recall`, `eve.specialists.permissions.permission_denial`.
- Produces:
  - `delegate_coding_task` — LangChain tool, args `(repos: list[str], goal: str, agent: str | None, model: str | None)`
  - `check_coding_session` — LangChain tool, no args
  - `send_to_coding_session` — LangChain tool, args `(session_id: str, message: str)`
  - `PERMISSION = "code.delegate"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coding_dispatch.py`:

```python
"""Mirrors tests/test_computer_dispatch.py. The load-bearing test in this
file is the first one: a denied member's request must never reach the box,
which is ADR 0006's whole pattern."""

from unittest.mock import AsyncMock

import pytest

from eve.coding import dispatch


def _config(permissions=("code.delegate",), sub="sub-noah", thread="t1"):
    return {"configurable": {"member": {"sub": sub, "permissions": list(permissions)},
                             "thread_id": thread}}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(dispatch, "create_coding_session", AsyncMock(return_value="ok"))
    monkeypatch.setattr(dispatch.store, "create_session", AsyncMock())
    monkeypatch.setattr(dispatch.store, "live_sessions_for", AsyncMock(return_value=[]))
    monkeypatch.setattr(dispatch.catalogue, "validate", AsyncMock(return_value="chatgpt/gpt-5.6-sol"))
    monkeypatch.setattr(dispatch, "_recall_context", AsyncMock(return_value="ctx"))
    monkeypatch.setattr(dispatch, "prompt_coding_session", AsyncMock(return_value="ok"))


async def test_a_member_without_the_permission_never_reaches_the_box():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "config": _config(permissions=[])}
    )

    assert "Permission denied" in result
    dispatch.create_coding_session.assert_not_awaited()


async def test_dispatch_returns_immediately_and_records_the_session():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "config": _config()}
    )

    assert "on it" in result.lower()
    dispatch.store.create_session.assert_awaited_once()
    kwargs = dispatch.store.create_session.await_args.kwargs
    assert kwargs["member_sub"] == "sub-noah"
    assert kwargs["repos"] == ["acme/repo"]
    assert kwargs["context"] == "ctx"


async def test_the_agent_falls_back_to_the_configured_tiebreak(monkeypatch):
    monkeypatch.setenv("EVE_CODING_DEFAULT_AGENT", "codex")
    from eve.settings import get_settings

    get_settings.cache_clear()

    await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "config": _config()}
    )

    assert dispatch.store.create_session.await_args.kwargs["agent"] == "codex"
    get_settings.cache_clear()


async def test_an_agent_eve_names_is_honoured():
    await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "agent": "claude", "config": _config()}
    )

    assert dispatch.store.create_session.await_args.kwargs["agent"] == "claude"


async def test_an_unknown_agent_is_refused_before_the_http_call():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "agent": "cursor", "config": _config()}
    )

    assert "cursor" in result
    dispatch.create_coding_session.assert_not_awaited()


async def test_the_model_is_validated_before_dispatch():
    await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "model": "gpt-9-ultra", "config": _config()}
    )

    dispatch.catalogue.validate.assert_awaited_once_with("gpt-9-ultra", "codex")
    assert dispatch.store.create_session.await_args.kwargs["model"] == "chatgpt/gpt-5.6-sol"


async def test_no_repos_is_refused():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": [], "goal": "fix it", "config": _config()}
    )

    assert "repo" in result.lower()
    dispatch.create_coding_session.assert_not_awaited()


async def test_a_box_failure_leaves_no_orphan_row():
    dispatch.create_coding_session.return_value = "error: eve-computer unavailable"

    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "config": _config()}
    )

    assert result.startswith("error:")
    dispatch.store.create_session.assert_not_awaited()


async def test_check_lists_only_the_asking_members_sessions():
    dispatch.store.live_sessions_for.return_value = [
        {"id": "abcdef12-3456", "repos": ["acme/repo"], "goal": "fix it",
         "status": "running", "agent": "codex", "model": "m"}
    ]
    monkeypatched = AsyncMock(return_value={"activity": ["tool: edit README"]})
    dispatch.get_coding_session = monkeypatched

    result = await dispatch.check_coding_session.ainvoke({"config": _config()})

    dispatch.store.live_sessions_for.assert_awaited_once_with("sub-noah")
    assert "abcdef12" in result
    assert "edit README" in result


async def test_check_denies_a_member_without_the_permission():
    result = await dispatch.check_coding_session.ainvoke({"config": _config(permissions=[])})

    assert "Permission denied" in result


async def test_an_interjection_is_sent_as_an_interjection():
    monkeypatch_get = AsyncMock(return_value={"id": "s1", "member_sub": "sub-noah"})
    dispatch.store.get = monkeypatch_get

    result = await dispatch.send_to_coding_session.ainvoke(
        {"session_id": "s1", "message": "use httpx", "config": _config()}
    )

    dispatch.prompt_coding_session.assert_awaited_once_with("s1", "use httpx", kind="interjection")
    assert "pass that on" in result.lower() or "told" in result.lower()


async def test_a_member_cannot_interject_into_another_members_session():
    dispatch.store.get = AsyncMock(return_value={"id": "s1", "member_sub": "sub-kendra"})

    result = await dispatch.send_to_coding_session.ainvoke(
        {"session_id": "s1", "message": "use httpx", "config": _config(sub="sub-noah")}
    )

    assert "don't have a session" in result.lower() or "not yours" in result.lower()
    dispatch.prompt_coding_session.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_coding_dispatch.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve.coding.dispatch'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/coding/dispatch.py`:

```python
"""Eve's three coding tools. Permission is checked here, before the HTTP
call, so a denied request never reaches eve-computer at all - ADR 0006's
pattern, applied a fourth time.

WHY THE ROW IS WRITTEN AFTER THE BOX ACCEPTS. A row for a session the box
never heard of would be polled forever by the supervisor and eventually
reported to the member as stale, for work that never started. Order
matters; `dispatch_computer_task` does the same thing for the same reason.

WHY RECALL HAPPENS HERE AND ONLY HERE. The supervisor runs every ~20s and
needs household context to answer the agent's questions - that is the whole
reason it lives in Eve's container and not on the box. A hybrid recall per
tick would be indefensible, so it is taken once, now, and snapshotted onto
the row for every later supervisor call to reuse.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.coding import catalogue, store
from eve.memory import store as memory_store
from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.tools_client import (
    create_coding_session,
    get_coding_session,
    prompt_coding_session,
)

logger = logging.getLogger(__name__)

PERMISSION = "code.delegate"
AGENTS = ("codex", "claude", "opencode")


async def _recall_context(goal: str, member_sub: str) -> str:
    """One recall, at creation, snapshotted onto the row for every later
    supervisor call to reuse.

    NOT `eve.memory.recall.recall` - that is a graph node taking (state,
    config), not a query function. The two primitives underneath it are what
    is wanted here: always-on memory carries the member's authored rules
    (which is how "use Claude Code for anything touching the graph" reaches
    this decision), and the lexical episodic arm cannot fail and needs no
    network call, unlike the vector arm.

    Degrades to empty rather than failing the dispatch: a session with no
    remembered preferences is worse than one with them, and far better than
    no session at all.
    """
    try:
        profile, household, _digest, rules = await memory_store.load_always_on(
            member_sub, None, include_rules=True
        )
        episodic = await memory_store.search_episodic_lexical(member_sub, goal, limit=10)
        lines = [
            m.content
            for m in (*rules, *profile, *household, *episodic)
            if getattr(m, "content", None)
        ]
        return "\n".join(lines)
    except Exception:
        logger.warning("recall for a coding session failed", exc_info=True)
        return ""


@tool
async def delegate_coding_task(
    repos: list[str],
    goal: str,
    config: RunnableConfig,
    agent: str | None = None,
    model: str | None = None,
) -> str:
    """Delegate a coding task to a dedicated coding agent working in a real
    git checkout on Eve's computer. Use for changes to source code that
    should end in a pull request.

    `repos` is one or more GitHub repositories ("owner/name" or just "name");
    pass several for a change that spans repositories, and they will share a
    branch name and produce one pull request each.

    `agent` chooses the harness: "codex" (rides the ChatGPT subscription,
    the default when nothing points elsewhere), "claude" (the strongest
    coder, metered spend), or "opencode". `model` is any model the LiteLLM
    proxy serves - pick a small fast one for a small change and a strong one
    for a hard change, and honour whatever the member has said they prefer.

    Returns immediately; the result is reported later, in a separate message.
    """
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), PERMISSION)
    if denial:
        return denial

    thread_id = config.get("configurable", {}).get("thread_id")
    if not thread_id:
        return "error: no thread to report the result on"
    if not repos:
        return "error: a coding session needs at least one repo to work in"

    chosen_agent = agent or get_settings().coding_default_agent
    if chosen_agent not in AGENTS:
        return f"error: unknown agent {chosen_agent!r}; expected one of {', '.join(AGENTS)}"

    chosen_model = await catalogue.validate(model, chosen_agent)
    session_id = str(uuid.uuid4())
    context = await _recall_context(goal, member["sub"])

    dispatched = await create_coding_session(
        session_id, chosen_agent, chosen_model, repos, goal
    )
    if dispatched.startswith("error:"):
        return dispatched

    await store.create_session(
        session_id=session_id,
        member_sub=member["sub"],
        thread_id=thread_id,
        goal=goal,
        agent=chosen_agent,
        model=chosen_model,
        repos=repos,
        context=context,
    )
    return "I'm on it — I'll let you know when it's done."


@tool
async def check_coding_session(config: RunnableConfig) -> str:
    """What Eve's delegated coding sessions are doing right now. Use when a
    member asks how something is going, or before sending a message into a
    session, since this is where the session ids come from."""
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), PERMISSION)
    if denial:
        return denial

    sessions = await store.live_sessions_for(member["sub"])
    if not sessions:
        return "No coding sessions are running."

    lines = []
    for row in sessions:
        live = await get_coding_session(row["id"]) or {}
        activity = "; ".join(live.get("activity", [])[-3:]) or "no activity yet"
        lines.append(
            f"- {row['id'][:8]} ({row['agent']}/{row['model']}) on "
            f"{', '.join(row['repos'])}: {row['goal']} — {row['status']}. {activity}"
        )
    return "\n".join(lines)


@tool
async def send_to_coding_session(
    session_id: str, message: str, config: RunnableConfig
) -> str:
    """Pass a member's message into a running coding session - a correction,
    a preference, an answer. Get `session_id` from check_coding_session. The
    message is delivered at the end of the agent's current turn, not
    mid-turn."""
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), PERMISSION)
    if denial:
        return denial

    row = await store.get(session_id)
    if row is None or row["member_sub"] != member["sub"]:
        # Deliberately one message for both cases: whether another member
        # has a session by this id is not this member's business.
        return "I don't have a session by that id."

    sent = await prompt_coding_session(session_id, message, kind="interjection")
    if sent.startswith("error:"):
        return sent
    return "I'll pass that on at the end of its current step."
```

Note: `check_coding_session` accepts a short id in conversation but `send_to_coding_session` needs the full one. If the eight-character prefix proves awkward in practice, resolve prefixes in `store.get`; do not add a second lookup tool.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_coding_dispatch.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add src/eve/coding/dispatch.py tests/test_coding_dispatch.py
git commit -m "feat(eve-acp): add the three coding tools behind code.delegate"
```

---
## Task 11: The supervisor

The control loop, and the only place an `idle` turn is ever classified. One LLM call per idle session per tick, on `Tier.CODE`.

**Files:**
- Create: `src/eve/coding/supervisor.py`
- Test: `tests/test_coding_supervisor.py`

**Interfaces:**
- Consumes: `store.*` (Task 7), `tools_client.*` (Task 8), `eve.models.get_model`, `eve.models.Tier`.
- Produces:
  - `Decision` — Pydantic model, fields `action: Literal["reply", "done", "escalate"]`, `text: str`.
  - `async tick(now: datetime | None = None) -> list[dict]` — the sessions that resolved on this tick.
  - `async decide(row: dict, turns: list[dict], pending: list[str]) -> Decision`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coding_supervisor.py`:

```python
"""The four-way decision, and what each outcome does to the row.

The test that matters most is `test_escalate_parks_the_session_alive`:
escalating and then discarding the session would throw away the very thing
the member's answer is for, and the two features would not compose."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from eve.coding import supervisor
from eve.coding.supervisor import Decision


def _row(session_id="s1", status="running", cursor=0, updated_at=None):
    return {
        "id": session_id, "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "fix the CalDAV client", "agent": "codex", "model": "m",
        "repos": ["acme/repo"], "context": "Noah prefers httpx.",
        "status": status, "cursor": cursor, "supervisor_turns": 0,
        "updated_at": updated_at or datetime.now(UTC),
        "created_at": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setenv("EVE_CODING_SESSION_STALE_MINUTES", "60")
    monkeypatch.setenv("EVE_CODING_MAX_SUPERVISOR_TURNS", "3")
    monkeypatch.setenv("EVE_CODING_SESSION_TIMEOUT_SECONDS", "28800")
    from eve.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(supervisor.store, "live_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(supervisor.store, "advance_cursor", AsyncMock())
    monkeypatch.setattr(supervisor.store, "mark_resolved", AsyncMock())
    monkeypatch.setattr(supervisor.store, "set_status", AsyncMock())
    monkeypatch.setattr(supervisor.store, "bump_supervisor_turns", AsyncMock(return_value=1))
    monkeypatch.setattr(supervisor, "prompt_coding_session", AsyncMock(return_value="ok"))
    monkeypatch.setattr(supervisor, "close_coding_session", AsyncMock(return_value={"prs": []}))
    monkeypatch.setattr(supervisor, "kill_coding_session", AsyncMock(return_value="ok"))
    yield
    get_settings.cache_clear()


def _box(status="idle", turns=None, pending=None, cursor=2):
    return {
        "status": status,
        "turns": turns if turns is not None else [{"role": "agent", "text": "Which auth library?"}],
        "pending": pending or [],
        "cursor": cursor,
        "activity": [],
        "error": "",
    }


async def test_a_running_session_is_left_alone(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box("running")))
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    assert await supervisor.tick() == []
    decide.assert_not_awaited()


async def test_reply_sends_the_composed_prompt_and_keeps_the_session_live(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=Decision(action="reply", text="Use httpx.")),
    )

    resolved = await supervisor.tick()

    supervisor.prompt_coding_session.assert_awaited_once_with("s1", "Use httpx.", kind="reply")
    supervisor.store.advance_cursor.assert_awaited_once_with("s1", 2)
    assert resolved == []


async def test_done_closes_the_session_and_reports_the_prs(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(
        supervisor, "decide", AsyncMock(return_value=Decision(action="done", text="All set."))
    )
    supervisor.close_coding_session.return_value = {
        "prs": [{"repo": "acme/repo", "commits": 2, "pr_url": "https://x/1"}]
    }

    resolved = await supervisor.tick()

    supervisor.close_coding_session.assert_awaited_once_with("s1")
    assert resolved[0]["status"] == "finished"
    assert resolved[0]["result"]["prs"][0]["pr_url"] == "https://x/1"


async def test_escalate_parks_the_session_alive(monkeypatch):
    """The subprocess and the worktrees stay up so the member's answer can
    resume this same session through send_to_coding_session."""
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=Decision(action="escalate", text="Which staging DB?")),
    )

    resolved = await supervisor.tick()

    supervisor.close_coding_session.assert_not_awaited()
    supervisor.store.set_status.assert_awaited_once_with("s1", "blocked")
    assert resolved[0]["status"] == "blocked"
    assert "Which staging DB?" in resolved[0]["result"]["question"]


async def test_an_already_blocked_session_is_not_re_escalated(monkeypatch):
    """It is waiting on a human. Asking again every 20 seconds would be a
    notification loop, not a conversation."""
    supervisor.store.live_sessions.return_value = [_row(status="blocked")]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    assert await supervisor.tick() == []
    decide.assert_not_awaited()


async def test_a_pending_interjection_reaches_the_decision(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value=_box(pending=["use httpx instead"])),
    )
    decide = AsyncMock(return_value=Decision(action="reply", text="Switch to httpx."))
    monkeypatch.setattr(supervisor, "decide", decide)

    await supervisor.tick()

    assert decide.await_args.args[2] == ["use httpx instead"]


async def test_a_blocked_session_wakes_when_a_member_interjects(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row(status="blocked")]
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value=_box(pending=["the staging one"])),
    )
    monkeypatch.setattr(
        supervisor, "decide", AsyncMock(return_value=Decision(action="reply", text="Use staging."))
    )

    await supervisor.tick()

    supervisor.prompt_coding_session.assert_awaited_once_with("s1", "Use staging.", kind="reply")


async def test_a_failed_session_on_the_box_resolves_as_failed(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={**_box(status="failed"), "error": "agent stopped: refusal"}),
    )

    resolved = await supervisor.tick()

    assert resolved[0]["status"] == "failed"
    assert "refusal" in resolved[0]["result"]["error"]


async def test_a_box_that_stops_answering_goes_stale_after_the_timeout(monkeypatch):
    stale = datetime.now(UTC) - timedelta(minutes=90)
    supervisor.store.live_sessions.return_value = [_row(updated_at=stale)]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=None))

    resolved = await supervisor.tick()

    assert resolved[0]["status"] == "stale"


async def test_a_box_that_stops_answering_briefly_is_left_alone(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=None))

    assert await supervisor.tick() == []


async def test_blowing_the_supervisor_budget_parks_and_asks_in_english(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    supervisor.store.bump_supervisor_turns.return_value = 4  # cap is 3
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    resolved = await supervisor.tick()

    decide.assert_not_awaited()
    assert resolved[0]["status"] == "blocked"
    assert "back and forth" in resolved[0]["result"]["question"]


async def test_a_session_past_its_wall_clock_bound_is_closed_out(monkeypatch):
    """The spec's per-session wall-clock bound. Without it a session parked
    on `blocked` that nobody ever answers sits live forever, holding a
    subprocess, a worktree, and a semaphore slot."""
    old = datetime.now(UTC) - timedelta(hours=9)
    supervisor.store.live_sessions.return_value = [{**_row(status="blocked"), "created_at": old}]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(supervisor, "kill_coding_session", AsyncMock(return_value="ok"))
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    resolved = await supervisor.tick()

    decide.assert_not_awaited()
    supervisor.kill_coding_session.assert_awaited_once_with("s1")
    assert resolved[0]["status"] == "failed"
    assert "too long" in resolved[0]["result"]["error"]


async def test_a_session_inside_its_wall_clock_bound_is_left_running(monkeypatch):
    supervisor.store.live_sessions.return_value = [
        {**_row(), "created_at": datetime.now(UTC) - timedelta(minutes=5)}
    ]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box("running")))

    assert await supervisor.tick() == []


async def test_one_bad_session_does_not_stop_the_others(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row("s1"), _row("s2")]

    async def _get(session_id, since=0):
        if session_id == "s1":
            raise RuntimeError("boom")
        return _box(status="failed")

    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(side_effect=_get))

    resolved = await supervisor.tick()

    assert [r["id"] for r in resolved] == ["s2"]


async def test_decide_asks_tier_code_and_gets_a_structured_answer(monkeypatch):
    """The decision is one call, on the tier that exists for code."""
    captured = {}

    class FakeModel:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return self

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return Decision(action="done", text="Opened the PR.")

    def _get_model(tier):
        captured["tier"] = tier
        return FakeModel()

    monkeypatch.setattr(supervisor, "get_model", _get_model)

    decision = await supervisor.decide(
        _row(), [{"role": "agent", "text": "Opened PR #4."}], []
    )

    from eve.models import Tier

    assert captured["tier"] is Tier.CODE
    assert captured["schema"] is Decision
    assert decision.action == "done"
    # The recall snapshot and the goal both reach the model.
    prompt = str(captured["messages"])
    assert "Noah prefers httpx." in prompt
    assert "fix the CalDAV client" in prompt


async def test_a_decision_call_that_fails_leaves_the_session_alone(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(supervisor, "decide", AsyncMock(side_effect=RuntimeError("model down")))

    assert await supervisor.tick() == []
    supervisor.store.mark_resolved.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_coding_supervisor.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'eve.coding.supervisor'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/coding/supervisor.py`:

```python
"""The control loop. The only place an `idle` turn is ever classified.

WHY THIS IS NOT AN AMBIENT SOURCE. It runs on its own ~20s interval rather
than the 300s ambient tick because there is an agent waiting on the other
end of it. Five minutes of latency per conversational turn would make Eve a
worse correspondent than the member who delegated the work.

WHY IT LIVES IN EVE'S CONTAINER. The decision needs the goal, the member's
remembered preferences, and the household. eve-computer holds none of those
and is never going to (design doc: "the box learns nothing about the
family"). Only the composed prompt text crosses back.

WHY RECALL IS NOT DONE HERE. `row["context"]` is a snapshot taken once, at
dispatch. This function runs every twenty seconds per live session; a hybrid
recall per tick would be indefensible, and reusing the snapshot is what makes
running the supervisor on this side affordable at all.

ESCALATE DOES NOT RESOLVE THE SESSION. It parks it: the subprocess and the
worktrees stay up so the member's answer can resume this same session
through `send_to_coding_session`. Escalating and then discarding would throw
away the very thing the answer is for.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from eve.coding import store
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve.tools_client import (
    close_coding_session,
    get_coding_session,
    kill_coding_session,
    prompt_coding_session,
)

logger = logging.getLogger(__name__)


class Decision(BaseModel):
    action: Literal["reply", "done", "escalate"] = Field(
        description=(
            "reply: answer the agent and let it keep working. "
            "done: the work is complete - close the session and open the pull requests. "
            "escalate: the agent needs something only the family member can answer."
        )
    )
    text: str = Field(
        description=(
            "For reply, the exact message to send to the coding agent. "
            "For done, a one-line summary for the family member. "
            "For escalate, the question to put to the family member."
        )
    )


_SYSTEM = """You delegated a coding task to a coding agent and are reading its latest turn.

Decide one of three things:
- reply: you can answer or redirect it yourself. Answer from the goal and from what you remember about the household. Be specific and brief.
- done: the work is finished. Say so.
- escalate: it needs a decision only the family member can make - a credential, a product choice, a preference you have never been told.

Prefer reply. Escalating a question you could have answered wastes the delegation. Claiming done when the work is unfinished is worse than either."""


async def decide(row: dict, turns: list[dict], pending: list[str]) -> Decision:
    transcript = "\n".join(f"{t['role']}: {t['text']}" for t in turns)
    interjections = (
        "\n\nThe family member just said this. It takes priority over your own "
        "judgement - work it into your reply:\n" + "\n".join(f"- {m}" for m in pending)
        if pending
        else ""
    )
    prompt = (
        f"{_SYSTEM}\n\n"
        f"The goal you delegated: {row['goal']}\n"
        f"Repositories: {', '.join(row['repos'])}\n\n"
        f"What you remember that might bear on this:\n{row['context']}\n\n"
        f"New turns from the agent:\n{transcript}"
        f"{interjections}"
    )
    model = get_model(Tier.CODE).with_structured_output(Decision)
    return await model.ainvoke([HumanMessage(content=prompt)])


async def tick(now: datetime | None = None) -> list[dict]:
    """Returns the sessions that resolved - finished, failed, blocked, or
    stale - on this tick. `eve_ambient.sources.coding.poll` turns each into
    a Signal."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    stale_after = timedelta(minutes=settings.coding_session_stale_minutes)
    resolved: list[dict] = []

    for row in await store.live_sessions():
        try:
            outcome = await _advance(row, now, stale_after, settings)
        except Exception:
            # One session's box hiccup, one model outage, or one malformed
            # row must not stop every other session from being checked.
            logger.warning("supervising session %s raised", row["id"], exc_info=True)
            continue
        if outcome is not None:
            resolved.append(outcome)

    return resolved


def _resolved(row: dict, status: str, result: dict, now: datetime) -> dict:
    return {**row, "status": status, "result": result, "finished_at": now}


async def _advance(row: dict, now, stale_after, settings) -> dict | None:
    # The outermost bound, checked before anything else so an expired
    # session cannot spend one more model call on its way out. A session
    # parked on `blocked` is running nothing the box could time out; it is
    # waiting on a human, and some humans never answer.
    age = (now - row["created_at"]).total_seconds()
    if age > settings.coding_session_timeout_seconds:
        await kill_coding_session(row["id"])
        result = {"error": f"the session ran too long ({int(age)}s) and was stopped"}
        await store.mark_resolved(row["id"], "failed", result)
        return _resolved(row, "failed", result, now)

    box = await get_coding_session(row["id"], since=row["cursor"])

    if box is None:
        if now - row["updated_at"] > stale_after:
            await store.mark_resolved(row["id"], "stale", {})
            return _resolved(row, "stale", {}, now)
        return None

    status = box.get("status")
    if status == "failed":
        result = {"error": box.get("error") or "the session failed"}
        await store.mark_resolved(row["id"], "failed", result)
        return _resolved(row, "failed", result, now)
    if status == "killed":
        await store.mark_resolved(row["id"], "failed", {"error": "the session was killed"})
        return _resolved(row, "failed", {"error": "the session was killed"}, now)
    if status != "idle":
        return None

    pending = box.get("pending") or []
    # A blocked session is waiting on a human. Re-deciding every twenty
    # seconds would be a notification loop, not a conversation - so it only
    # wakes when the member actually says something.
    if row["status"] == "blocked" and not pending:
        return None

    turns = box.get("turns") or []
    if not turns and not pending:
        return None

    count = await store.bump_supervisor_turns(row["id"])
    if count > settings.coding_max_supervisor_turns:
        # graph.py's _LOOP_EXHAUSTED, one level out: whatever the budget is,
        # a loop that blows it has to answer in English rather than stall.
        question = (
            "I've been going back and forth with the coding agent on this "
            f"without reaching an answer: {row['goal']}. Could you take a look?"
        )
        await store.set_status(row["id"], "blocked")
        return _resolved(row, "blocked", {"question": question}, now)

    decision = await decide(row, turns, pending)
    await store.advance_cursor(row["id"], box.get("cursor", row["cursor"]))

    if decision.action == "reply":
        sent = await prompt_coding_session(row["id"], decision.text, kind="reply")
        if sent.startswith("error:"):
            logger.warning("could not deliver a reply to session %s", row["id"])
            return None
        await store.set_status(row["id"], "running")
        return None

    if decision.action == "escalate":
        await store.set_status(row["id"], "blocked")
        return _resolved(row, "blocked", {"question": decision.text}, now)

    closed = await close_coding_session(row["id"]) or {"prs": []}
    result = {"summary": decision.text, **closed}
    await store.mark_resolved(row["id"], "finished", result)
    return _resolved(row, "finished", result, now)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_coding_supervisor.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/eve/coding/supervisor.py tests/test_coding_supervisor.py
git commit -m "feat(eve-acp): add the supervisor, the only place an idle turn is classified"
```

---

## Task 12: Reporting back

The ambient source, its permission mapping, the relevance-filter bypass, and the loop that drives the supervisor. `gates.py` fails closed on an unmapped source, so forgetting the mapping would silently notify nobody.

**Files:**
- Create: `src/eve_ambient/sources/coding.py`
- Modify: `src/eve_ambient/gates.py`
- Modify: `src/eve_ambient/pipeline.py`
- Modify: `src/eve_ambient/app.py`
- Test: `tests/test_ambient_sources_coding.py`
- Test: `tests/test_ambient_gates.py` (extend)

**Interfaces:**
- Consumes: `supervisor.tick` (Task 11), `store.recently_resolved_sessions` (Task 7).
- Produces: `async poll(_member_sub: str) -> list[Signal]` with `source="coding"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ambient_sources_coding.py`:

```python
"""Mirrors tests/test_ambient_sources_computer.py, including the 24-hour
re-derivation window: a signal suppressed by quiet hours or the daily cap
must be re-derivable on a later tick rather than lost the moment
supervisor.tick() stops returning it."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import coding


def _session(session_id="s1", status="finished", result=None):
    return {
        "id": session_id, "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "fix the CalDAV client", "repos": ["acme/repo"],
        "status": status, "result": result if result is not None else {"prs": []},
        "finished_at": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(coding.supervisor, "tick", AsyncMock(return_value=[]))
    monkeypatch.setattr(coding.coding_store, "recently_resolved_sessions", AsyncMock(return_value=[]))


async def test_a_finished_session_names_its_pull_requests():
    coding.supervisor.tick.return_value = [
        _session(result={"summary": "done", "prs": [
            {"repo": "acme/repo", "commits": 2, "pr_url": "https://x/1"}
        ]})
    ]

    signals = await coding.poll("sub-noah")

    assert signals[0].source == "coding"
    assert "https://x/1" in signals[0].summary
    assert signals[0].payload["thread_id"] == "t1"


async def test_a_session_with_no_commits_says_so_rather_than_claiming_success():
    coding.supervisor.tick.return_value = [
        _session(result={"summary": "nothing to change", "prs": [
            {"repo": "acme/repo", "commits": 0, "pr_url": None}
        ]})
    ]

    signals = await coding.poll("sub-noah")

    assert "no changes" in signals[0].summary.lower()


async def test_a_blocked_session_carries_its_question():
    coding.supervisor.tick.return_value = [
        _session(status="blocked", result={"question": "Which staging DB?"})
    ]

    signals = await coding.poll("sub-noah")

    assert "Which staging DB?" in signals[0].summary


async def test_a_failed_session_reports_the_error():
    coding.supervisor.tick.return_value = [
        _session(status="failed", result={"error": "agent stopped: refusal"})
    ]

    assert "refusal" in (await coding.poll("sub-noah"))[0].summary


async def test_a_stale_session_says_it_never_reported_back():
    coding.supervisor.tick.return_value = [_session(status="stale", result=None)]

    assert "never reported back" in (await coding.poll("sub-noah"))[0].summary


async def test_a_suppressed_signal_is_re_derived_from_the_recent_window():
    coding.supervisor.tick.return_value = []
    coding.coding_store.recently_resolved_sessions.return_value = [_session()]

    assert len(await coding.poll("sub-noah")) == 1


async def test_this_ticks_row_wins_over_the_recent_window():
    fresh = _session(result={"summary": "fresh", "prs": []})
    coding.supervisor.tick.return_value = [fresh]
    coding.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"summary": "stale copy", "prs": []})
    ]

    signals = await coding.poll("sub-noah")

    assert len(signals) == 1
    assert "fresh" in signals[0].summary
```

Append to `tests/test_ambient_gates.py`:

```python
def test_coding_is_mapped_to_its_permission():
    """gates.py fails closed on an unmapped source. Without this entry every
    delegated coding result would silently notify nobody."""
    from eve_ambient.gates import SOURCE_PERMISSION

    assert SOURCE_PERMISSION["coding"] == "code.delegate"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambient_sources_coding.py tests/test_ambient_gates.py -v`
Expected: FAIL — missing module, and `KeyError: 'coding'`

- [ ] **Step 3: Add the permission mapping**

Edit `src/eve_ambient/gates.py`. In `SOURCE_PERMISSION`, after `"computer": "computer.use",` add:

```python
    "coding": "code.delegate",
```

- [ ] **Step 4: Add the relevance-filter bypass**

Edit `src/eve_ambient/pipeline.py`. Every place that currently special-cases `signal.source == "computer"` must treat `coding` identically — a delegated coding result was explicitly requested by a member, and an LLM deciding a direct request is "not relevant" and swallowing it is the worst available failure mode.

Introduce a module-level constant near the top and use it at all four sites (lines ~41, ~51, ~111, ~125-138 in the current file):

```python
# Signals a member explicitly asked for, rather than guesses about what they
# might want to know. The relevance filter is bypassed for these: an LLM
# deciding the answer to a direct request is "not relevant" and swallowing
# it is the worst failure mode available.
_REQUESTED_SOURCES = ("computer", "coding")
```

Then replace `signal.source == "computer"` with `signal.source in _REQUESTED_SOURCES` at each site, and update the `why=` string at line ~51 to `"a family member asked for this directly"`.

- [ ] **Step 5: Write the ambient source**

Create `src/eve_ambient/sources/coding.py`:

```python
"""Resolved coding sessions as signals.

Two deliberate deviations from how other ambient sources behave, both
inherited from sources/computer.py and both for its reasons:

The relevance filter is bypassed (`pipeline` special-cases this source).
Every other signal is a guess about what the family might want to know;
this one was explicitly requested.

`per_member=False`: eve-computer holds no per-member data, so this is
polled once per tick for the whole household - each resolved session's
member comes from Eve's own row, not from the box.

The 24-hour merge is the same re-derivation window computer.py documents: a
signal whose delivery was suppressed (quiet hours, the daily cap) or
deferred (a transient push failure) is re-derived on a later tick instead of
being lost the moment supervisor.tick() stops returning it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eve.coding import store as coding_store
from eve.coding import supervisor
from eve_ambient.types import Signal

_LOOKBACK = timedelta(hours=24)


def _summary(row: dict) -> str:
    goal = row["goal"]
    result = row["result"] or {}

    if row["status"] == "stale":
        return f"A coding session went stale and never reported back: {goal}"
    if row["status"] == "blocked":
        return f"The coding agent needs an answer on {goal}: {result.get('question', '')}".rstrip()
    if row["status"] == "failed" or result.get("error"):
        return f"A coding session failed: {goal}. {result.get('error', '')}".rstrip()

    prs = [pr for pr in result.get("prs", []) if pr.get("pr_url")]
    if not prs:
        return f"Finished {goal}, but it made no changes, so there's no pull request."
    links = "; ".join(f"{pr['repo']}: {pr['pr_url']}" for pr in prs)
    return f"Finished {goal}. {result.get('summary', '')} Pull requests: {links}".strip()


async def poll(_member_sub: str) -> list[Signal]:
    resolved = await supervisor.tick()
    since = datetime.now(UTC) - _LOOKBACK
    recent = await coding_store.recently_resolved_sessions(since=since)

    by_id: dict[str, dict] = {}
    for row in (*resolved, *recent):
        # `resolved` first: on the tick a session actually transitions its
        # row is fresher than whatever Postgres reads back a moment later,
        # and dict insertion order keeps the first write for a given id.
        by_id.setdefault(row["id"], row)

    return [
        Signal(
            source="coding",
            key=row["id"],
            occurred_at=row["finished_at"],
            member_sub=row["member_sub"],
            summary=_summary(row),
            payload={
                "thread_id": row["thread_id"],
                "goal": row["goal"],
                "repos": row["repos"],
                "result": row["result"],
                "status": row["status"],
            },
            cooldown_hours=24,
        )
        for row in by_id.values()
    ]
```

- [ ] **Step 6: Register the source and the supervisor loop**

Edit `src/eve_ambient/app.py`.

Register `coding` alongside `computer` in whatever list of sources `poll_once` iterates, following exactly the shape `computer` uses (`per_member=False`), and gate it on `settings.coding_enabled` the way `computer` is gated on `computer_enabled`.

Then add the second loop. After `_poll_forever`, add:

```python
async def _supervise_forever() -> None:
    """The coding supervisor's own tick, deliberately not the ambient one.

    This is a control loop with an agent waiting on the other end, not a
    notification pipeline: 300s of latency per conversational turn would
    make Eve a worse correspondent than the member who delegated the work.

    It only drives conversations forward. Resolved sessions are turned into
    signals by `sources.coding.poll` on the ambient tick, which is where the
    permission gate, the quiet hours, and the daily cap live - none of which
    a control loop has any business bypassing.
    """
    interval = get_settings().coding_supervisor_interval_seconds
    while True:
        try:
            await supervisor.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Same posture as _poll_forever: the loop is the last line of
            # defence and never dies.
            logger.exception("the coding supervisor tick failed outright")
        await asyncio.sleep(interval)
```

Import `from eve.coding import supervisor` at the top, and in `lifespan` start it beside the ambient task:

```python
    supervisor_task = None
    if settings.coding_enabled:
        supervisor_task = asyncio.create_task(_supervise_forever())
        logger.info(
            "coding supervisor ticking every %ss",
            settings.coding_supervisor_interval_seconds,
        )
```

and cancel it in the shutdown half, exactly as the ambient task is cancelled.

**Note the deliberate double-call:** both `_supervise_forever` and `sources.coding.poll` call `supervisor.tick()`. That is safe and intended — `tick()` is idempotent over already-resolved sessions because `live_sessions()` excludes them, and the ambient path is what carries a resolution through the permission gate. The fast loop advances conversations; the slow loop reports outcomes.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_sources_coding.py tests/test_ambient_gates.py tests/test_ambient_app.py tests/test_ambient_pipeline.py -v`
Expected: PASS, including the existing ambient suites — the `_REQUESTED_SOURCES` change must not alter `computer`'s behaviour.

- [ ] **Step 8: Commit**

```bash
git add src/eve_ambient/ tests/test_ambient_sources_coding.py tests/test_ambient_gates.py
git commit -m "feat(eve-acp): report resolved sessions and run the supervisor loop"
```

---

## Task 13: Wiring

The tools onto the graph, the permission onto the roster.

**Files:**
- Modify: `src/eve/graph.py`
- Modify: `family.yaml`
- Test: `tests/test_graph.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph.py`, matching how the existing `dispatch_computer_task` binding test is written (find it first — `grep -n "dispatch_computer_task" tests/test_graph.py`):

```python
def test_the_coding_tools_are_bound_when_coding_is_enabled(monkeypatch):
    monkeypatch.setenv("EVE_CODING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()

    names = {tool.name for tool in graph._static_tools({"configurable": {}})}

    assert {"delegate_coding_task", "check_coding_session", "send_to_coding_session"} <= names
    get_settings.cache_clear()


def test_the_coding_tools_are_absent_when_coding_is_disabled(monkeypatch):
    monkeypatch.setenv("EVE_CODING_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    names = {tool.name for tool in graph._static_tools({"configurable": {}})}

    assert "delegate_coding_task" not in names
    get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_graph.py -k coding -v`
Expected: FAIL — the tools are not bound.

- [ ] **Step 3: Bind the tools**

Edit `src/eve/graph.py`. Beside `from eve.computer.dispatch import dispatch_computer_task` (line 44), add:

```python
from eve.coding.dispatch import (
    check_coding_session,
    delegate_coding_task,
    send_to_coding_session,
)
```

In `_static_tools`, beside the existing `computer_enabled` branch around line 100:

```python
    if settings.coding_enabled:
        tools.extend(
            [delegate_coding_task, check_coding_session, send_to_coding_session]
        )
```

Match the surrounding code's shape exactly — read lines 90-105 first and follow whatever the `computer_enabled` branch does rather than the sketch above.

- [ ] **Step 4: Grant the permission**

Edit `family.yaml`. Under Noah's `permissions`, after `- computer.use`, add:

```yaml
      # EVE-4: delegating coding work to Claude Code, Codex, or OpenCode,
      # which open pull requests under Eve's own GitHub account.
      - code.delegate
```

Leave Kendra's list unchanged. This is the grant that decides who can spend the subscription's rate limits and open pull requests; widening it is a pull request of its own.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_graph.py tests/test_family.py -v`
Expected: PASS

- [ ] **Step 6: Full unit suite**

Run: `uv run pytest -m "not integration and not live and not docker" -q`
Expected: PASS, everything.

- [ ] **Step 7: Commit**

```bash
git add src/eve/graph.py family.yaml tests/test_graph.py
git commit -m "feat(eve-acp): bind the coding tools and grant code.delegate to Noah"
```

---

## Task 14: The documents this changes

Every design in this repo lands its reasoning somewhere durable. Three documents plus a new ADR.

**Files:**
- Create: `docs/adr/0016-the-box-runs-the-protocol-eve-holds-the-judgement.md`
- Modify: `docs/architecture.md`
- Modify: `README.md`
- Modify: `docs/adr/0004-model-tier-routing.md`

- [ ] **Step 1: Write ADR 0016**

Create `docs/adr/0016-the-box-runs-the-protocol-eve-holds-the-judgement.md`:

```markdown
# 16. The box runs the protocol, Eve holds the judgement

**Status:** Accepted
**Date:** 2026-09-01

## Context

EVE-4 gives Eve real conversations with coding agents over ACP. A
conversation needs someone deciding what an agent's turn *means*: whether
"which auth library do you want?" is a question to answer, a sign the task
was underspecified, or the last thing before it opens a pull request.

ACP offers no help here. `session/prompt` returns a `stop_reason` and
nothing else; "the turn ended" is the only fact on the wire. Something has
to classify it.

eve-computer's standing invariant is that the box learns nothing about the
family - no member subject, no roster, no permissions. That invariant is
what bounds the blast radius of a machine with a shell, passwordless sudo,
and Eve's own GitHub credential.

## Decision

The box records; Eve classifies.

A turn that ends leaves the session `idle` on the box, forever, whatever it
contains. Eve's container reads the turn and decides reply, done, or
escalate, with the goal, the member, the thread, and a recall snapshot in
front of it. Only the composed prompt text crosses back.

`eve_computer/acp/session.py` therefore contains no branch on what an agent
said, and adding one is the design going wrong.

## Consequences

**The supervisor needs its own tick.** An agent waiting on an answer cannot
wait 300 seconds, so the loop runs at ~20s inside eve-ambient, separate from
the ambient poll. Two loops in one process, with two different reasons to
exist.

**Recall is snapshotted, not repeated.** A hybrid recall every twenty
seconds per live session would be indefensible, so it is taken once at
dispatch and stored on the row. This is the cost of putting the judgement in
Eve's container, paid once per session instead of once per tick.

**Eve's session vocabulary is wider than the box's.** The box has `idle`;
Eve has `blocked`, which the box could never produce, because deciding a
question is unanswerable requires the member.

**The alternative was worse in both directions.** A supervisor loop on the
box would answer with no family context - the invariant working as intended,
producing a worse correspondent than the member who delegated the work.
Proxying raw ACP to Eve's container would give her `session/update` push,
but `session/update` is server-push, and accepting it inverts the
one-directional network rule the whole eve-computer safety argument rests
on.
```

- [ ] **Step 2: Amend ADR 0004**

Append to `docs/adr/0004-model-tier-routing.md`:

```markdown
## Amendment (2026-09-01, EVE-4)

Delegated coding sessions are a new and much heavier consumer of the same
ChatGPT subscription this ADR routes the tiers through. Three things follow.

**Codex through LiteLLM still rides the subscription.** LiteLLM fronts the
ChatGPT/Codex sign-in itself, so pointing `codex-acp` at the proxy keeps
eve-computer's zero-metered-spend property rather than abandoning it. Same
for OpenCode on a `chatgpt/*` model. The one agent with real metered spend
is Claude Code, on `anthropic/claude-sonnet-5`. Codex is therefore the
tiebreak agent when nothing else points anywhere.

**Rate limits, not dollars, are the thing to watch.** `REFLEX` was moved off
this credential precisely so it would not consume the limits Noah uses for
his own work. A coding agent running for half an hour is a far heavier
consumer than any chat turn. The session bounds are the throttle.

**The refused model set is not enumerated anywhere in the repository.** The
probe above found `gpt-5.3-codex`, `-codex-spark`, `-instant`,
`-chat-latest`, and `gpt-5.4-pro` all rejected by the sign-in, and LiteLLM
still lists them - so Eve can pick one and it will fail. That is accepted:
those failures are LOUD, at the first prompt, with the backend saying why.
Only `ocp/*` is denied (`eve/coding/catalogue.py`), because it fails
SILENTLY - the proxy strips tool definitions, and a coding agent that cannot
call tools answers fluently and changes nothing. Loud failures need no
registry; silent ones do.
```

- [ ] **Step 3: Extend `docs/architecture.md`**

Find the `eve-computer` section (around line 467, where the Codex CLI note lives) and add, in that file's voice:

- the session lane beside the task lane, and why coding sessions are not serialised behind the GUI queue
- the `src/eve_computer/acp/` module map and the `src/eve/coding/` module map
- the supervisor loop as the second `asyncio` loop inside `eve-ambient`, with its own interval
- the `eve_coding_session` table beside `eve_computer_task`
- `code.delegate` in the permission list
- a pointer to the design doc and ADR 0016

- [ ] **Step 4: Update the README**

In "Where the program ends", under the second boundary, add a paragraph:

```markdown
EVE-4 extends this once more, and the consequence is worth stating rather
than discovering: **Eve can now open pull requests against this
repository.** The README says a tool needing a secret is "an `eve-tools`
handler in a pull request, forever." She can now write that pull request.

This does not weaken the boundary; it routes through it. The gate was never
"Eve cannot propose" - it was "a human merges." That gate is exactly where
it was, and unlike the `propose_tool` interrupt, this one is a code review
in GitHub with a diff, CI, and no 11pm approval prompt. It is a better
instance of the same gate.
```

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0016-the-box-runs-the-protocol-eve-holds-the-judgement.md \
        docs/adr/0004-model-tier-routing.md docs/architecture.md README.md
git commit -m "docs(eve-acp): record ADR 0016 and the documents EVE-4 changes"
```

---

## Task 15: Integration and live tiers

The unit tier mocks the box everywhere. These two tiers are the only things entitled to claim the protocol, the routing, and the boundary actually work.

**Files:**
- Create: `tests/test_coding_integration.py`
- Create: `tests/test_coding_live.py`
- Modify: `tests/test_computer_live.py` (extend the unreachability assertion)

- [ ] **Step 1: Write the integration test**

Create `tests/test_coding_integration.py`:

```python
"""The full session lifecycle against the real HTTP surface, the real ACP
protocol, and a real git repo - with a STUB ACP agent rather than a model.

The stub is the point, not a compromise. It makes the protocol handshake,
the worktree, the branch, the commit, and the push assertable
deterministically, which no test driving a real model can be. Whether a
real agent reaches LiteLLM is tests/test_coding_live.py's job, and that is
the only file entitled to claim it.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

AUTH = {"Authorization": "Bearer secret"}

# A minimal ACP agent: on each prompt it commits one file into the worktree
# and ends its turn. Written to disk and spawned as a real subprocess, so
# the stdio JSON-RPC transport is exercised rather than mocked.
STUB_AGENT = r'''
import asyncio, pathlib, subprocess, sys
import acp
from acp import Agent
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse


class Stub(Agent):
    def __init__(self, conn):
        self._conn = conn
        self._cwd = None
        self._n = 0

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocol_version=acp.PROTOCOL_VERSION)

    async def new_session(self, cwd, **kwargs):
        self._cwd = cwd
        return NewSessionResponse(session_id="stub-1")

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[0].text
        # session.py sends the operator hint first; it is not a task.
        if not text.startswith("You are working in a git worktree"):
            self._n += 1
            tree = pathlib.Path(self._cwd) / "repo"
            (tree / ("stub-%d.txt" % self._n)).write_text(text)
            subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
            subprocess.run(
                ["git", "-c", "user.email=e@x", "-c", "user.name=E",
                 "commit", "-m", "stub: " + text[:30]],
                cwd=tree, check=True,
            )
        await self._conn.session_update(
            session_id=session_id,
            update=acp.update_agent_message_text("did: " + text[:40]),
        )
        return PromptResponse(stop_reason="end_turn")


asyncio.run(acp.run_agent(Stub, sys.stdin, sys.stdout))
'''


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path):
    bare = tmp_path / "origin" / "repo.git"
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
    return bare


@pytest.fixture
def box(tmp_path, origin, monkeypatch):
    """eve-computer, wired to a stub agent and a local origin."""
    stub = tmp_path / "stub_agent.py"
    stub.write_text(STUB_AGENT)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\necho "https://github.com/acme/repo/pull/1"\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "secret")
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_GITHUB_OWNER", "acme")

    from eve_computer import app as app_mod
    from eve_computer.acp import repo, session
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    session._SESSIONS.clear()
    session._semaphore = None

    # The stub is registered by monkeypatching `build`, not by adding a
    # fourth registry entry: the registry has three entries because there
    # are three agents, and a test-only fourth in production code would be
    # exactly the plugin system the design refused.
    monkeypatch.setattr(session, "build", lambda agent, model: ([sys.executable, str(stub)], {}))
    monkeypatch.setattr(repo, "_clone_url", lambda name: str(origin))

    with TestClient(app_mod.app) as client:
        yield client

    get_computer_settings.cache_clear()
    session._SESSIONS.clear()


def _wait_for(client, session_id, status, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/sessions/{session_id}", headers=AUTH).json()
        if body["status"] == status:
            return body
        if body["status"] in ("failed", "killed"):
            pytest.fail(f"session ended as {body['status']}: {body.get('error')}")
        time.sleep(0.2)
    pytest.fail(f"session never reached {status}")


def test_a_session_runs_a_full_lifecycle(box):
    """Create, turn, idle, reply, close - ending in a real branch with real
    commits pushed to a real origin."""
    created = box.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "m",
              "repos": ["acme/repo"], "prompt": "write the first file"},
        headers=AUTH,
    )
    assert created.status_code == 202

    first = _wait_for(box, "s1", "idle")
    assert any("did: write the first file" in t["text"] for t in first["turns"])

    box.post("/sessions/s1/prompt", json={"text": "write a second file"}, headers=AUTH)
    _wait_for(box, "s1", "idle")

    # The cursor returns only what is new.
    delta = box.get(f"/sessions/s1?since={first['cursor']}", headers=AUTH).json()
    assert all("first file" not in t["text"] for t in delta["turns"])

    closed = box.post("/sessions/s1/close", headers=AUTH).json()
    assert closed["prs"][0]["commits"] == 2
    assert closed["prs"][0]["pr_url"] == "https://github.com/acme/repo/pull/1"


def test_two_sessions_share_a_repo_without_colliding(box):
    for session_id in ("s1", "s2"):
        box.post(
            "/sessions",
            json={"id": session_id, "agent": "codex", "model": "m",
                  "repos": ["acme/repo"], "prompt": f"work for {session_id}"},
            headers=AUTH,
        )

    a = _wait_for(box, "s1", "idle")
    b = _wait_for(box, "s2", "idle")

    assert a["branch"] != b["branch"]
    assert box.post("/sessions/s1/close", headers=AUTH).json()["prs"][0]["commits"] == 1
    assert box.post("/sessions/s2/close", headers=AUTH).json()["prs"][0]["commits"] == 1


def test_an_interjection_is_recorded_and_not_delivered_by_the_box(box):
    box.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "m",
              "repos": ["acme/repo"], "prompt": "go"},
        headers=AUTH,
    )
    _wait_for(box, "s1", "idle")

    box.post(
        "/sessions/s1/prompt",
        json={"text": "use httpx instead", "kind": "interjection"},
        headers=AUTH,
    )

    body = box.get("/sessions/s1", headers=AUTH).json()
    assert body["pending"] == ["use httpx instead"]
    assert body["status"] == "idle"  # the box did NOT act on it


def test_a_session_with_no_commits_produces_no_pull_request(box):
    box.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "m",
              "repos": ["acme/repo"], "prompt": "go"},
        headers=AUTH,
    )
    _wait_for(box, "s1", "idle")

    # Undo the stub's commit so the branch matches its base.
    from eve_computer.acp import repo as repo_mod

    tree = repo_mod.worktree_path(
        Path(os.environ["EVE_COMPUTER_SESSIONS_DIR"]) / "s1", "acme/repo"
    )
    _run("git", "reset", "--hard", "origin/main", cwd=tree)

    closed = box.post("/sessions/s1/close", headers=AUTH).json()

    assert closed["prs"][0] == {"repo": "acme/repo", "commits": 0, "pr_url": None}
```

Before running, verify the stub against the installed SDK — `acp.run_agent`'s signature and `acp.update_agent_message_text`'s return type — exactly as Task 1 verified the client side:

```bash
uv run --group computer python -c "import acp, inspect; print(inspect.signature(acp.run_agent)); print(inspect.signature(acp.update_agent_message_text))"
```

Adjust the stub to what is actually there. Guessing this API is how a plan produces a test that cannot run.

- [ ] **Step 2: Run it**

Run: `uv run --group computer pytest tests/test_coding_integration.py -m integration -v`
Expected: PASS (4 tests). No Postgres needed — this tier exercises the box, which holds its sessions in memory.

- [ ] **Step 3: Write the live test**

Create `tests/test_coding_live.py`:

```python
"""The only tier entitled to claim an agent x model pair works.

ADR 0004's original fallback plan died on an untested assumption about
exactly this kind of wire translation, and the fix was a live probe, not a
table. That is why there is no compatibility matrix in this repository -
there is this file.

Requires EVE_LIVE_TESTS=1, the real LiteLLM proxy, and a scratch repo named
by EVE_CODING_LIVE_REPO that Eve's GitHub account can push to.

This tier spends real subscription rate limits - the exact resource ADR
0004 protects - so the parametrisation stays small on purpose.
"""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("EVE_LIVE_TESTS") != "1", reason="live tier"),
]

REPO = os.environ.get("EVE_CODING_LIVE_REPO", "")
AUTH = {"Authorization": f"Bearer {os.environ.get('EVE_COMPUTER_API_KEY', '')}"}


@pytest.fixture
def box():
    from eve_computer import app as app_mod
    from eve_computer.acp import session

    session._SESSIONS.clear()
    session._semaphore = None
    with TestClient(app_mod.app) as client:
        yield client
    session._SESSIONS.clear()


@pytest.mark.skipif(not REPO, reason="set EVE_CODING_LIVE_REPO")
@pytest.mark.parametrize(
    ("agent", "model"),
    [
        ("codex", "chatgpt/gpt-5.6-sol"),
        ("codex", "chatgpt/gpt-5.6-luna"),
        ("claude", "anthropic/claude-sonnet-5"),
        ("opencode", "chatgpt/gpt-5.6-sol"),
    ],
)
def test_the_agent_reaches_litellm_and_completes_a_turn(box, agent, model):
    """A `wire_api` or provider-block mistake surfaces here and nowhere
    else. The goal is deliberately trivial: this asserts the pair TALKS,
    not that it codes well."""
    session_id = str(uuid.uuid4())
    created = box.post(
        "/sessions",
        json={
            "id": session_id, "agent": agent, "model": model, "repos": [REPO],
            "prompt": "Reply with the single word READY. Do not edit any files.",
        },
        headers=AUTH,
    )
    assert created.status_code == 202

    deadline = time.monotonic() + 300
    body: dict = {}
    while time.monotonic() < deadline:
        body = box.get(f"/sessions/{session_id}", headers=AUTH).json()
        if body["status"] in ("idle", "failed"):
            break
        time.sleep(2)

    box.delete(f"/sessions/{session_id}", headers=AUTH)

    assert body.get("status") == "idle", (
        f"{agent} on {model} did not complete a turn: {body.get('error')}"
    )
    assert any(t["role"] == "agent" and t["text"].strip() for t in body["turns"]), (
        f"{agent} on {model} produced no text - the classic signature of a "
        "proxy that accepted the request and stripped what mattered"
    )


async def test_the_real_catalogue_contains_no_ocp_models():
    """The deny-list, against the real proxy. ocp/* fails silently at
    runtime - the proxy strips tool definitions - so it must be refused
    before dispatch, not discovered afterwards."""
    from eve.coding import catalogue

    catalogue._reset_cache()
    models = await catalogue.available_models()

    assert models, "the real proxy returned no models"
    assert not [m for m in models if m.startswith("ocp/")]


async def test_every_parametrised_model_is_still_served():
    """Catches a model retirement (ADR 0004: `gpt-5.4` retired 2026-08-31)
    before it shows up as an unexplained session failure."""
    from eve.coding import catalogue

    catalogue._reset_cache()
    models = await catalogue.available_models()

    for model in ("chatgpt/gpt-5.6-sol", "chatgpt/gpt-5.6-luna", "anthropic/claude-sonnet-5"):
        assert model in models, f"{model} is no longer served by the proxy"
```

- [ ] **Step 4: Extend the unreachability test**

Open `tests/test_computer_live.py` and confirm the assertion that Postgres, `eve-tools`, `eve-sandbox`, Eve's API, and the Kubernetes API server are unreachable from inside the box still passes unchanged.

**Do not weaken it.** It now guards three coding agents with a shell instead of one, and every safety claim in the design reduces to it. If it fails, the NetworkPolicy was loosened and that is the finding, not the test.

- [ ] **Step 5: Full suite, every tier**

```bash
uv run pytest -m "not integration and not live and not docker" -q
docker compose -f docker-compose.test.yml up -d && uv run pytest -m integration -q
uv run pytest -m docker -q
EVE_LIVE_TESTS=1 uv run pytest -m live -q
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_coding_integration.py tests/test_coding_live.py
git commit -m "test(eve-acp): add the integration lifecycle and the live routing matrix"
```

---

## Definition of done

From the spec, restated as things to check by hand once the tasks are green:

1. A member with `code.delegate` asks Eve for a code change; she dispatches, answers immediately, and later reports a pull request link in her own voice.
2. All three agents complete a session and are provably served by LiteLLM; Codex and OpenCode add no metered spend.
3. Eve picks different agents and models for a one-line fix and a multi-file refactor, and an authored rule stating a preference changes what she picks.
4. A model registered in LiteLLM after this ships is usable with no change in this repository.
5. An `ocp/*` model is refused at dispatch; a wire-incompatible pair fails at the first prompt.
6. An agent asks a clarifying question mid-session; Eve answers it herself without involving the member.
7. A member says "tell it to use httpx instead"; that lands in the agent's next prompt and changes the outcome.
8. An agent asks something Eve cannot answer; the session parks, the member is asked, their answer resumes the same session.
9. Two sessions run in parallel on the same repository without colliding, and neither blocks a GUI task on `/tasks`.
10. A session spanning two repositories produces two pull requests on one branch name.
11. A wiped PVC recovers all three model-routing config files from `bootstrap.sh` with no human involved.
12. The `eve-computer` unreachability test still passes.
