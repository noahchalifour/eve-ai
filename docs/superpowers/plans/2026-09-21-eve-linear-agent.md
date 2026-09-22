# Eve as a Linear Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person delegates an issue to Eve in Linear and she runs a real coding session on `eve-computer`, narrating progress as Linear Agent Activities and ending in a pull request.

**Architecture:** A Linear agent session is a second reporting channel on a coding session Eve already knows how to run. `eve-ambient` gains `POST /signals/linear` beside its Home Assistant webhook (verify, 202, background task). `eve-tools` gains a `linear.*` handler family holding the OAuth token, per ADR 0006. Two nullable columns on `eve_coding_session` carry the Linear side, and the existing supervisor loop emits an activity at each transition it already computes.

**Tech Stack:** Python 3.12, FastAPI, httpx, psycopg (async), Alembic, pydantic-settings, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-21-eve-linear-agent-design.md`

## Global Constraints

- **Style:** no em dashes in prose or comments; the repo uses ` - ` or recast sentences. Match surrounding code.
- **Comments explain why, not what.** This codebase's modules open with a docstring explaining the load-bearing decision. Follow that; a task that adds a non-obvious ordering or constraint explains it in place.
- **Every Linear emission degrades to a logged warning.** No activity call may raise into a caller. Losing narration is acceptable; killing a running coding session is not.
- **`compare_digest` on bytes, never `str`.** It raises `TypeError` on a non-ASCII `str` operand.
- **No credential in `eve-ambient`.** The Linear API token is read only by `src/eve_tools/`. `eve-ambient` holds the webhook signing secret only.
- **The repo allowlist is the injection boundary.** It is read from settings, never from webhook payload content.
- **Tests:** default tier is unit. `pytest` from the repo root. Async tests need no decorator (`asyncio_mode = "auto"`).
- **Settings caches are `lru_cache`d.** Any test mutating env must `get_settings.cache_clear()`; `tests/conftest.py` does this autouse, and `tests/test_ambient_app.py` shows the per-module pattern.
- **Alembic head is `0010_eve_widget_resource`.** The new migration's `down_revision` is that string.

---

## File Structure

**Create:**
- `src/eve_tools/linear_client.py` - the only holder of the Linear API token. GraphQL calls: create activity, move issue status, set delegate.
- `src/eve_linear/__init__.py` - package marker.
- `src/eve_linear/verify.py` - signature and timestamp verification. Pure functions, no IO.
- `src/eve_linear/identity.py` - Linear user to family member, permission check, repo resolution. Pure functions, no IO.
- `src/eve_linear/activities.py` - activity payload builders and the emit wrapper that swallows failures.
- `src/eve_linear/types.py` - the parsed webhook event.
- `src/eve_linear/handler.py` - the background task: acknowledge, gate, dispatch. Knows the ordering contract.
- `alembic/versions/0011_eve_coding_session_linear.py`
- `tests/test_linear_verify.py`, `tests/test_linear_identity.py`, `tests/test_linear_activities.py`, `tests/test_linear_handler.py`, `tests/test_linear_integration.py`

**Modify:**
- `src/eve/settings.py` - six settings plus validation in `model_post_init`.
- `src/eve/family.py` - `linear_id` on `Member`, `by_linear_id()` on `Family`.
- `src/eve/coding/store.py` - Linear columns on insert; `get_by_linear_session`; `touch_linear_emitted`.
- `src/eve/coding/supervisor.py` - emit at each transition; heartbeat.
- `src/eve_ambient/app.py` - the endpoint.
- `src/eve_tools/app.py` - the `linear.*` handlers.
- `src/eve_tools/settings.py` - `linear_api_token`.
- `family.yaml` - documented `linear_id` key.
- `docs/architecture.md` - a "Linear" section.
- `docs/adr/0006-eve-tools-isolation.md` - the inbound-initiated amendment.

**Why `src/eve_linear/` rather than inside `src/eve_ambient/`:** the handler is imported by `eve-ambient` (the webhook) and the emitter by `eve` (the supervisor, which runs in the same process as the ambient loop but is `eve`'s module). A shared package avoids `eve` importing from `eve_ambient`, which nothing does today and which would invert the existing dependency direction.

---

### Task 1: Settings and family mapping

**Files:**
- Modify: `src/eve/settings.py` (add after the coding block ending line 213; validation in `model_post_init`)
- Modify: `src/eve/family.py:22-73`
- Modify: `src/eve_tools/settings.py`
- Modify: `family.yaml`
- Test: `tests/test_settings.py`, `tests/test_family.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.linear_enabled: bool`, `.linear_webhook_secret: str`, `.linear_repo_allowlist: list[str]`, `.linear_heartbeat_minutes: int`, `.linear_max_live_sessions: int`; `ToolsSettings.linear_api_token: str`; `Member.linear_id: str | None`; `Family.by_linear_id(linear_id: str) -> Member | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_family.py`:

```python
def test_member_carries_an_optional_linear_id(tmp_path):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'abc'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_123'\n"
        "    permissions: ['code.delegate']\n"
        "  - sub: 'def'\n"
        "    name: 'Kendra'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    permissions: []\n"
    )
    family = Family.from_yaml(roster)
    assert family.get("abc").linear_id == "lin_123"
    assert family.get("def").linear_id is None


def test_by_linear_id_finds_the_member_or_returns_none(tmp_path):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'abc'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_123'\n"
        "    permissions: []\n"
    )
    family = Family.from_yaml(roster)
    assert family.by_linear_id("lin_123").sub == "abc"
    assert family.by_linear_id("lin_nope") is None
    # An unmapped member must never be reachable by a falsy lookup.
    assert family.by_linear_id("") is None
```

In `tests/test_settings.py`:

```python
def test_linear_enabled_requires_its_secret_token_and_allowlist(monkeypatch):
    from eve.settings import Settings

    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", "s" * 32)
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')
    # No allowlist is the injection boundary missing entirely.
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", "[]")
    with pytest.raises(ValueError, match="EVE_LINEAR_REPO_ALLOWLIST"):
        Settings()


def test_linear_enabled_requires_a_webhook_secret(monkeypatch):
    from eve.settings import Settings

    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')
    monkeypatch.delenv("EVE_LINEAR_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValueError, match="EVE_LINEAR_WEBHOOK_SECRET"):
        Settings()


def test_linear_webhook_secret_must_not_be_guessable(monkeypatch):
    from eve.settings import Settings

    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", "short")
    with pytest.raises(ValueError, match="at least 32"):
        Settings()


def test_linear_is_off_by_default(monkeypatch):
    from eve.settings import Settings

    for name in (
        "EVE_LINEAR_ENABLED",
        "EVE_LINEAR_WEBHOOK_SECRET",
        "EVE_LINEAR_REPO_ALLOWLIST",
    ):
        monkeypatch.delenv(name, raising=False)
    assert Settings().linear_enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_family.py -k linear tests/test_settings.py -k linear -v`
Expected: FAIL with `AttributeError` / `TypeError` on `linear_id` and `by_linear_id`.

- [ ] **Step 3: Implement**

In `src/eve/family.py`, add to `Member` after `wardrobe_album` (line 33):

```python
    # The Linear user id this member signs in as, when the workspace is
    # installed (EVE-26). Optional: a member without one cannot delegate
    # from Linear, which is a refusal rather than an error. Non-secret and
    # per-member, so it belongs in the roster rather than in settings.
    linear_id: str | None = None
```

In `Family.__init__` (line 40), build the second index:

```python
    def __init__(self, members: list[Member]) -> None:
        self._by_sub = {m.sub: m for m in members}
        # Only members who actually have one. A `None` key would make an
        # absent id collide with an unmapped lookup.
        self._by_linear_id = {m.linear_id: m for m in members if m.linear_id}
```

In `Family.from_yaml`'s `Member(...)` call (line 48), add:

```python
                    linear_id=entry.get("linear_id") or None,
```

Add the lookup after `get` (line 68):

```python
    def by_linear_id(self, linear_id: str) -> Member | None:
        """`None` rather than raising: an unmapped Linear user is a refusal
        Eve explains in the session, not an exception. An empty id can never
        match, because `_by_linear_id` holds no falsy keys."""
        if not linear_id:
            return None
        return self._by_linear_id.get(linear_id)
```

In `src/eve/settings.py`, after the coding block (line 213):

```python
    # EVE-26 (Linear). See docs/superpowers/specs/
    # 2026-09-21-eve-linear-agent-design.md.
    #
    # Off by default, like ambient_enabled and coding_enabled: this subsystem
    # acts on input from outside the household, so a deployment that has not
    # deliberately enabled it must refuse every webhook.
    linear_enabled: bool = False
    # Held by eve-ambient, which verifies with it. NOT a credential for
    # reaching Linear - that token lives only in eve-tools (ADR 0006).
    linear_webhook_secret: str = ""
    # The containment boundary for prompt injection. Issue text reaches an
    # agent that writes code and opens pull requests; the one thing that text
    # can never widen is which repos are reachable, because this is read from
    # settings rather than from anything Linear sent.
    linear_repo_allowlist: list[str] = []
    # Against Linear's 30-minute stale threshold. A coding agent can work
    # longer than that without producing a supervisor decision, and a
    # stale-looking session invites a human to intervene in work going fine.
    linear_heartbeat_minutes: int = 10
    # Concurrent Linear-originated coding sessions. Five people delegating at
    # once should queue, not fan out to five agents on one box.
    linear_max_live_sessions: int = 3
```

In `model_post_init`, after the `computer_api_key` checks:

```python
        if self.linear_webhook_secret and len(self.linear_webhook_secret) < 32:
            raise ValueError(
                "EVE_LINEAR_WEBHOOK_SECRET must be at least 32 characters: it "
                "is the only thing distinguishing Linear from anyone who "
                "knows the URL, so a guessable value fails open"
            )
        if self.linear_enabled:
            # Enabled-but-unconfigured accepts webhooks, spends a dispatch,
            # then fails every emission on a 401 while Linear retries - the
            # least diagnosable failure this subsystem can have.
            if not self.linear_webhook_secret:
                raise ValueError(
                    "EVE_LINEAR_WEBHOOK_SECRET is required when "
                    "EVE_LINEAR_ENABLED=true"
                )
            if not self.linear_repo_allowlist:
                raise ValueError(
                    "EVE_LINEAR_REPO_ALLOWLIST is required when "
                    "EVE_LINEAR_ENABLED=true: it is the only boundary between "
                    "an issue anyone can file and a repo Eve can write to"
                )
```

In `src/eve_tools/settings.py`, add to `ToolsSettings`:

```python
    # EVE-26: the actor=app OAuth token for the Linear workspace install.
    # A plain setting rather than an oauth_store row because an app install
    # is a one-time workspace grant with no refresh cycle, and that store
    # exists to manage per-member refreshable grants.
    linear_api_token: str = ""
```

In `family.yaml`, under Noah's entry, after `wardrobe_album`:

```yaml
    # EVE-26: the Linear user id this member delegates from. Find it with
    # `linear api '{ viewer { id } }'` while signed in as them. A Linear
    # user with no mapping here is refused, so adding a teammate is a pull
    # request against this file, same as every other grant.
    linear_id: ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_family.py tests/test_settings.py -v`
Expected: PASS, including the pre-existing tests in both files.

- [ ] **Step 5: Commit**

```bash
git add src/eve/settings.py src/eve/family.py src/eve_tools/settings.py family.yaml tests/test_family.py tests/test_settings.py
git commit -m "feat(linear): settings and the linear_id family mapping"
```

---

### Task 2: Webhook verification

**Files:**
- Create: `src/eve_linear/__init__.py`, `src/eve_linear/verify.py`
- Test: `tests/test_linear_verify.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `verify_signature(raw_body: bytes, header: str | None, secret: str) -> bool`; `timestamp_is_fresh(webhook_timestamp_ms: int | None, now_ms: int | None = None, window_seconds: int = 60) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
import hashlib
import hmac
import time

from eve_linear.verify import timestamp_is_fresh, verify_signature

SECRET = "s" * 32


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correct_signature_verifies():
    body = b'{"action":"created"}'
    assert verify_signature(body, _sign(body), SECRET) is True


def test_a_wrong_signature_is_refused():
    body = b'{"action":"created"}'
    assert verify_signature(body, _sign(body, "other" * 10), SECRET) is False


def test_a_missing_header_is_refused():
    assert verify_signature(b"{}", None, SECRET) is False
    assert verify_signature(b"{}", "", SECRET) is False


def test_an_empty_secret_is_refused():
    body = b"{}"
    assert verify_signature(body, _sign(body), "") is False


def test_a_non_ascii_header_is_refused_rather_than_raising():
    # compare_digest raises TypeError on a str operand containing non-ASCII,
    # and a raise here is a 500, which tells Linear to retry a request that
    # should have been refused outright.
    assert verify_signature(b"{}", "\u00ff" * 64, SECRET) is False


def test_a_non_hex_header_is_refused_rather_than_raising():
    assert verify_signature(b"{}", "zzzz", SECRET) is False


def test_the_signature_is_over_raw_bytes_not_reserialized_json():
    # Linear signs the exact bytes sent. Re-serializing parsed JSON changes
    # the spacing and therefore the MAC.
    raw = b'{"a": 1,  "b": 2}'
    reserialized = b'{"a":1,"b":2}'
    signature = _sign(raw)
    assert verify_signature(raw, signature, SECRET) is True
    assert verify_signature(reserialized, signature, SECRET) is False


def test_a_fresh_timestamp_passes_and_a_stale_one_does_not():
    now_ms = int(time.time() * 1000)
    assert timestamp_is_fresh(now_ms, now_ms) is True
    assert timestamp_is_fresh(now_ms - 59_000, now_ms) is True
    assert timestamp_is_fresh(now_ms - 61_000, now_ms) is False


def test_a_future_timestamp_outside_the_window_is_refused():
    now_ms = int(time.time() * 1000)
    assert timestamp_is_fresh(now_ms + 61_000, now_ms) is False


def test_a_missing_or_unusable_timestamp_is_refused():
    now_ms = int(time.time() * 1000)
    assert timestamp_is_fresh(None, now_ms) is False
    assert timestamp_is_fresh("not-a-number", now_ms) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_linear_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_linear'`.

- [ ] **Step 3: Implement**

Create `src/eve_linear/__init__.py` (empty file).

Create `src/eve_linear/verify.py`:

```python
"""Is this request actually from Linear, and is it recent?

Pure functions, no IO. This is the whole of the 5-second response path: a
webhook that cannot be verified must be refused without touching the
database, a model, or the network.

TWO CHECKS, NOT ONE. The signature stops a forged request. The timestamp
stops a replayed genuine one. Neither is redundant: a captured valid POST
replays forever against signature checking alone.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


def verify_signature(raw_body: bytes, header: str | None, secret: str) -> bool:
    """`raw_body` is the exact bytes received, never a re-serialization of
    parsed JSON: Linear signs what it sent, and `json.dumps` of the parsed
    object differs in whitespace and key order.

    Returns False rather than raising on every malformed input. A raise here
    becomes a 500, and a 500 tells Linear to retry a request that should have
    been refused permanently.
    """
    if not secret or not header:
        return False
    try:
        presented = bytes.fromhex(header)
    except ValueError:
        # Not hex at all. Includes the non-ASCII case, which would make
        # `compare_digest` raise TypeError on a str operand.
        return False
    computed = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(computed, presented)


def timestamp_is_fresh(
    webhook_timestamp_ms: int | None,
    now_ms: int | None = None,
    window_seconds: int = _WINDOW_SECONDS,
) -> bool:
    """Linear's `webhookTimestamp` is UNIX milliseconds. Absolute difference,
    so a clock ahead of ours is refused the same as one behind: both mean we
    cannot reason about freshness."""
    if webhook_timestamp_ms is None:
        return False
    try:
        sent = int(webhook_timestamp_ms)
    except (TypeError, ValueError):
        return False
    current = now_ms if now_ms is not None else int(time.time() * 1000)
    return abs(current - sent) <= window_seconds * 1000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_linear_verify.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_linear/__init__.py src/eve_linear/verify.py tests/test_linear_verify.py
git commit -m "feat(linear): webhook signature and timestamp verification"
```

---

### Task 3: Identity, permission, and repo resolution

**Files:**
- Create: `src/eve_linear/identity.py`
- Test: `tests/test_linear_identity.py`

**Interfaces:**
- Consumes: `Family.by_linear_id` (Task 1), `Settings.linear_repo_allowlist` (Task 1).
- Produces: `Refusal` (dataclass: `.kind: str` in `{"unmapped", "unpermitted", "no_repo"}`, `.message: str`); `resolve_member(linear_user_id: str) -> tuple[Member | None, Refusal | None]`; `resolve_repos(guidance: str | None, allowlist: list[str]) -> tuple[list[str], Refusal | None]`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from eve.family import get_family
from eve_linear.identity import resolve_member, resolve_repos


@pytest.fixture(autouse=True)
def roster(tmp_path, monkeypatch):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'noah-sub'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_noah'\n"
        "    permissions: ['code.delegate']\n"
        "  - sub: 'kendra-sub'\n"
        "    name: 'Kendra'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_kendra'\n"
        "    permissions: ['health']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


def test_a_mapped_member_with_the_permission_resolves():
    member, refusal = resolve_member("lin_noah")
    assert refusal is None
    assert member.sub == "noah-sub"


def test_an_unmapped_linear_user_is_refused():
    member, refusal = resolve_member("lin_stranger")
    assert member is None
    assert refusal.kind == "unmapped"
    # The message is shown to a human in Linear, so it must say what to do.
    assert "family.yaml" in refusal.message


def test_a_mapped_member_without_code_delegate_is_refused():
    member, refusal = resolve_member("lin_kendra")
    assert member is None
    assert refusal.kind == "unpermitted"
    assert "Kendra" in refusal.message


def test_an_empty_linear_id_is_refused_as_unmapped():
    member, refusal = resolve_member("")
    assert member is None
    assert refusal.kind == "unmapped"


def test_guidance_naming_an_allowed_repo_resolves_it():
    repos, refusal = resolve_repos(
        "Work in owner/repo for this team.", ["owner/repo", "owner/other"]
    )
    assert refusal is None
    assert repos == ["owner/repo"]


def test_guidance_naming_several_allowed_repos_resolves_all_of_them():
    repos, refusal = resolve_repos(
        "Repos: owner/other and owner/repo.", ["owner/repo", "owner/other"]
    )
    assert refusal is None
    # Allowlist order, so the result is deterministic regardless of how the
    # guidance was written.
    assert repos == ["owner/repo", "owner/other"]


def test_guidance_naming_a_repo_outside_the_allowlist_resolves_nothing():
    # The injection case: guidance is workspace-editable text, and naming a
    # repo there must never be enough to reach it.
    repos, refusal = resolve_repos("Use evil/backdoor.", ["owner/repo"])
    assert repos == []
    assert refusal.kind == "no_repo"


def test_empty_or_missing_guidance_is_refused_rather_than_guessed():
    for guidance in (None, "", "   "):
        repos, refusal = resolve_repos(guidance, ["owner/repo"])
        assert repos == []
        assert refusal.kind == "no_repo"
        assert "which repo" in refusal.message


def test_a_substring_of_an_allowed_repo_does_not_match():
    # "owner/rep" must not match "owner/repo", or a near-miss silently
    # widens the boundary.
    repos, refusal = resolve_repos("Use owner/rep.", ["owner/repo"])
    assert repos == []
    assert refusal.kind == "no_repo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_linear_identity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_linear.identity'`.

- [ ] **Step 3: Implement**

Create `src/eve_linear/identity.py`:

```python
"""Who is asking, may they ask, and what may Eve touch.

All three are pure local computation - a dict lookup, a frozenset membership
test, and a string scan over a configured list. That is what lets them run
ahead of the acknowledgement in handler.py: their result decides WHICH
activity to emit, and none of them can block on a network or a database.

THE ALLOWLIST IS THE INJECTION BOUNDARY. Guidance is text that anyone with
workspace access can edit, and an issue body is text anyone with a seat can
write. Both reach a coding agent. The one thing neither can do is widen which
repos are reachable, because `resolve_repos` only ever returns members of the
configured allowlist. Matching guidance against the allowlist (rather than
parsing repos out of guidance and checking them after) is what makes that
true by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eve.coding.dispatch import PERMISSION
from eve.family import Member, get_family


@dataclass(frozen=True)
class Refusal:
    """Why Eve will not start, in words a human reads in Linear."""

    kind: str  # "unmapped" | "unpermitted" | "no_repo"
    message: str


def resolve_member(linear_user_id: str) -> tuple[Member | None, Refusal | None]:
    """Exactly one of the two is None.

    Both refusals are terminal rather than answerable: no reply typed into
    Linear can add a roster entry or grant a permission, because both are
    pull requests against family.yaml by design.
    """
    member = get_family().by_linear_id(linear_user_id)
    if member is None:
        return None, Refusal(
            "unmapped",
            "I don't act for that Linear account. Mapping a Linear user to a "
            "family member is a change to `family.yaml`, so someone will need "
            "to open a pull request adding a `linear_id` for you.",
        )
    if not member.can(PERMISSION):
        return None, Refusal(
            "unpermitted",
            f"{member.name} doesn't have the `{PERMISSION}` permission, so I "
            "can't take on coding work for them. Granting it is a change to "
            "`family.yaml`.",
        )
    return member, None


def resolve_repos(
    guidance: str | None, allowlist: list[str]
) -> tuple[list[str], Refusal | None]:
    """Which allowed repos this guidance names, in allowlist order.

    Allowlist order rather than mention order so the result does not depend
    on how the guidance sentence was phrased.

    The word-boundary match is what stops `owner/rep` from resolving to
    `owner/repo`: a near-miss that silently widened the boundary would defeat
    the whole point of having one.
    """
    text = guidance or ""
    found = [
        repo
        for repo in allowlist
        if re.search(rf"(?<![\w/-]){re.escape(repo)}(?![\w/-])", text)
    ]
    if not found:
        return [], Refusal(
            "no_repo",
            "I can't tell which repo this should land in. Tell me which repo "
            "to work in, or add it to the team's guidance in Linear.",
        )
    return found, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_linear_identity.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_linear/identity.py tests/test_linear_identity.py
git commit -m "feat(linear): identity, permission, and allowlisted repo resolution"
```

---

### Task 4: The migration and store columns

**Files:**
- Create: `alembic/versions/0011_eve_coding_session_linear.py`
- Modify: `src/eve/coding/store.py:22-38` (`create_session`), and append two functions
- Test: `tests/test_coding_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `store.create_session(..., linear_session_id: str | None = None, linear_issue_id: str | None = None)`; `store.get_by_linear_session(linear_session_id: str) -> dict | None`; `store.touch_linear_emitted(session_id: str) -> None`; columns `linear_session_id`, `linear_issue_id`, `linear_emitted_at`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coding_store.py`, following that file's existing DB fixture pattern:

```python
async def test_a_linear_session_id_is_stored_and_looked_up():
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id,
        member_sub="noah-sub",
        thread_id="thread-1",
        goal="fix the thing",
        agent="dsh",
        model="claude-sonnet-5",
        repos=["owner/repo"],
        context="",
        linear_session_id="lin_sess_1",
        linear_issue_id="lin_issue_1",
    )
    row = await store.get_by_linear_session("lin_sess_1")
    assert row["id"] == session_id
    assert row["linear_issue_id"] == "lin_issue_1"


async def test_a_chat_dispatched_session_has_no_linear_columns():
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id,
        member_sub="noah-sub",
        thread_id="thread-1",
        goal="fix the thing",
        agent="dsh",
        model="claude-sonnet-5",
        repos=["owner/repo"],
        context="",
    )
    row = await store.get(session_id)
    assert row["linear_session_id"] is None
    assert row["linear_issue_id"] is None


async def test_two_sessions_cannot_share_one_linear_session_id():
    # This constraint IS the retry idempotency key. Linear retries on 5xx and
    # on timeout, and a retry that dispatches twice spends real money and
    # opens two pull requests for one request.
    await store.create_session(
        session_id=str(uuid.uuid4()),
        member_sub="noah-sub",
        thread_id="t",
        goal="g",
        agent="dsh",
        model="m",
        repos=["owner/repo"],
        context="",
        linear_session_id="lin_dupe",
    )
    with pytest.raises(Exception):
        await store.create_session(
            session_id=str(uuid.uuid4()),
            member_sub="noah-sub",
            thread_id="t",
            goal="g",
            agent="dsh",
            model="m",
            repos=["owner/repo"],
            context="",
            linear_session_id="lin_dupe",
        )


async def test_many_sessions_may_have_no_linear_session_id():
    # A unique constraint over NULLs must not make chat dispatch single-use.
    for _ in range(3):
        await store.create_session(
            session_id=str(uuid.uuid4()),
            member_sub="noah-sub",
            thread_id="t",
            goal="g",
            agent="dsh",
            model="m",
            repos=["owner/repo"],
            context="",
        )


async def test_get_by_linear_session_returns_none_when_unknown():
    assert await store.get_by_linear_session("lin_never") is None


async def test_touch_linear_emitted_advances_the_heartbeat_clock():
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id,
        member_sub="noah-sub",
        thread_id="t",
        goal="g",
        agent="dsh",
        model="m",
        repos=["owner/repo"],
        context="",
        linear_session_id="lin_touch",
    )
    before = (await store.get(session_id))["linear_emitted_at"]
    await store.touch_linear_emitted(session_id)
    after = (await store.get(session_id))["linear_emitted_at"]
    assert before is None
    assert after is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coding_store.py -k linear -v`
Expected: FAIL with `TypeError: create_session() got an unexpected keyword argument 'linear_session_id'`.

- [ ] **Step 3: Implement**

Create `alembic/versions/0011_eve_coding_session_linear.py`:

```python
"""The Linear side of a coding session.

Revision ID: 0011_eve_coding_session_linear
Revises: 0010_eve_widget_resource

TWO COLUMNS RATHER THAN A TABLE. The relationship is strictly one to one: a
Linear agent session drives exactly one coding session, and a coding session
has at most one Linear session. A separate table buys a join on that, in
exchange for a generalization that pays off only when a second ticketing
integration exists.

THE UNIQUE CONSTRAINT IS THE IDEMPOTENCY KEY, not an access path. Linear
retries on a 5xx and on a timeout; a retried `created` that dispatches a
second session spends real money and opens a second pull request for one
request. The insert conflict is the dedup, and unlike an in-memory guard it
survives a restart. Postgres treats NULLs as distinct in a unique index, so
every chat-dispatched session (which has no Linear side) is unaffected.
"""
from alembic import op

revision = "0011_eve_coding_session_linear"
down_revision = "0010_eve_widget_resource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE eve_coding_session
          ADD COLUMN linear_session_id text,
          ADD COLUMN linear_issue_id   text,
          ADD COLUMN linear_emitted_at timestamptz
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX eve_coding_session_linear_session"
        " ON eve_coding_session (linear_session_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS eve_coding_session_linear_session")
    op.execute(
        """
        ALTER TABLE eve_coding_session
          DROP COLUMN linear_session_id,
          DROP COLUMN linear_issue_id,
          DROP COLUMN linear_emitted_at
        """
    )
```

In `src/eve/coding/store.py`, replace `create_session` (lines 22-38) with:

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
    linear_session_id: str | None = None,
    linear_issue_id: str | None = None,
) -> None:
    """The two Linear columns are None for a chat-dispatched session, which
    is every session that existed before EVE-26. The unique index on
    `linear_session_id` is what makes a retried Linear webhook insert fail
    rather than dispatch a second agent."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_coding_session"
            " (id, member_sub, thread_id, goal, agent, model, repos, context,"
            "  status, linear_session_id, linear_issue_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s)",
            (
                session_id,
                member_sub,
                thread_id,
                goal,
                agent,
                model,
                Jsonb(repos),
                context,
                linear_session_id,
                linear_issue_id,
            ),
        )
```

Append to the same file:

```python
async def get_by_linear_session(linear_session_id: str) -> dict | None:
    """How a `prompted` webhook finds the session it is answering."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_coding_session WHERE linear_session_id = %s",
                (linear_session_id,),
            )
            return await cur.fetchone()


async def touch_linear_emitted(session_id: str) -> None:
    """Stamped after every successful emission. The heartbeat reads it to
    decide whether Linear has heard from us recently enough, so it must not
    be stamped for an emission that failed."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_coding_session SET linear_emitted_at = now() WHERE id = %s",
            (session_id,),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coding_store.py -v && pytest tests/test_alembic_graph.py -v`
Expected: PASS. `test_alembic_graph.py` confirms the revision chain has a single head.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0011_eve_coding_session_linear.py src/eve/coding/store.py tests/test_coding_store.py
git commit -m "feat(linear): coding session columns and the retry idempotency key"
```

---

### Task 5: The eve-tools Linear client

**Files:**
- Create: `src/eve_tools/linear_client.py`
- Modify: `src/eve_tools/app.py:33-63` (the `_HANDLERS` table)
- Test: `tests/test_eve_tools_linear.py`

**Interfaces:**
- Consumes: `ToolsSettings.linear_api_token` (Task 1).
- Produces: `linear_client.create_activity(session_id: str, content: dict) -> dict`; `linear_client.move_issue_to_started(issue_id: str, team_id: str) -> dict`; `linear_client.set_delegate(issue_id: str, actor_id: str) -> dict`. Tool names `linear.create_activity`, `linear.move_issue_to_started`, `linear.set_delegate`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from eve_tools import linear_client


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_LINEAR_API_TOKEN", "lin_api_token")
    from eve_tools.settings import get_tools_settings

    get_tools_settings.cache_clear()
    yield
    get_tools_settings.cache_clear()


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


async def test_create_activity_posts_the_mutation_with_the_token(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse(
                {"data": {"agentActivityCreate": {"success": True}}}
            )

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    result = await linear_client.create_activity(
        "sess-1", {"type": "thought", "body": "On it."}
    )

    assert result["success"] is True
    assert captured["headers"]["Authorization"] == "Bearer lin_api_token"
    assert captured["json"]["variables"]["input"]["agentSessionId"] == "sess-1"
    assert captured["json"]["variables"]["input"]["content"]["type"] == "thought"


async def test_a_graphql_error_body_is_raised_not_silently_treated_as_success(
    monkeypatch,
):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            # GraphQL answers 200 with an errors array. Treating that as
            # success would report a lost activity as delivered.
            return _FakeResponse({"errors": [{"message": "bad session id"}]})

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(linear_client.LinearError, match="bad session id"):
        await linear_client.create_activity("sess-1", {"type": "thought", "body": "x"})


async def test_a_missing_token_raises_rather_than_calling_linear(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_LINEAR_API_TOKEN", "")
    from eve_tools.settings import get_tools_settings

    get_tools_settings.cache_clear()

    with pytest.raises(linear_client.LinearError, match="not configured"):
        await linear_client.create_activity("sess-1", {"type": "thought", "body": "x"})


async def test_move_issue_to_started_picks_the_lowest_position_started_state(
    monkeypatch,
):
    calls = []

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            calls.append(json)
            if "states" in json["query"]:
                return _FakeResponse(
                    {
                        "data": {
                            "team": {
                                "states": {
                                    "nodes": [
                                        {"id": "s2", "name": "Started", "position": 2.0},
                                        {"id": "s1", "name": "Todo", "position": 1.0},
                                    ]
                                }
                            }
                        }
                    }
                )
            return _FakeResponse({"data": {"issueUpdate": {"success": True}}})

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    await linear_client.move_issue_to_started("issue-1", "team-1")

    assert calls[1]["variables"]["stateId"] == "s1"


async def test_move_issue_to_started_is_a_no_op_when_no_started_state_exists(
    monkeypatch,
):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            return _FakeResponse({"data": {"team": {"states": {"nodes": []}}}})

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    result = await linear_client.move_issue_to_started("issue-1", "team-1")
    assert result == {"success": False, "reason": "no started state"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eve_tools_linear.py -v`
Expected: FAIL with `ImportError: cannot import name 'linear_client'`.

- [ ] **Step 3: Implement**

Create `src/eve_tools/linear_client.py`:

```python
"""The only holder of the Linear API token.

ADR 0006: third-party credentials live in exactly one service. EVE-26 is the
first time a third party initiates contact with Eve rather than being polled,
and the rule survives it because verification and action are separable. The
webhook signing secret (which proves Linear reached us) lives in eve-ambient;
this token (which reaches Linear) lives only here.

GRAPHQL ANSWERS 200 WITH AN ERRORS ARRAY. Checking the HTTP status alone
would report a rejected mutation as a delivered activity, and the caller
would stamp a heartbeat for something Linear never received.
"""

from __future__ import annotations

import logging

import httpx

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.linear.app/graphql"
_TIMEOUT = 15.0

_CREATE_ACTIVITY = """
mutation AgentActivityCreate($input: AgentActivityCreateInput!) {
  agentActivityCreate(input: $input) { success }
}
"""

_STARTED_STATES = """
query TeamStartedStatuses($teamId: String!) {
  team(id: $teamId) {
    states(filter: { type: { eq: "started" } }) {
      nodes { id name position }
    }
  }
}
"""

_ISSUE_UPDATE_STATE = """
mutation IssueUpdate($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
}
"""

_ISSUE_UPDATE_DELEGATE = """
mutation IssueSetDelegate($id: String!, $delegateId: String!) {
  issueUpdate(id: $id, input: { delegateId: $delegateId }) { success }
}
"""


class LinearError(Exception):
    """Linear refused, or is unreachable, or is not configured."""


async def _call(query: str, variables: dict) -> dict:
    token = get_tools_settings().linear_api_token
    if not token:
        raise LinearError("the Linear API token is not configured")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            _API_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        body = response.json()
    if body.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in body["errors"])
        raise LinearError(messages)
    return body.get("data") or {}


async def create_activity(session_id: str, content: dict) -> dict:
    data = await _call(
        _CREATE_ACTIVITY,
        {"input": {"agentSessionId": session_id, "content": content}},
    )
    return data.get("agentActivityCreate") or {"success": False}


async def move_issue_to_started(issue_id: str, team_id: str) -> dict:
    """The team's lowest-position `started` state, which is what Linear's
    best practices specify. A team with no started state is a no-op rather
    than an error: the delegation is still valid, the board just has an
    unusual workflow."""
    data = await _call(_STARTED_STATES, {"teamId": team_id})
    nodes = ((data.get("team") or {}).get("states") or {}).get("nodes") or []
    if not nodes:
        return {"success": False, "reason": "no started state"}
    target = min(nodes, key=lambda n: n.get("position", 0))
    updated = await _call(
        _ISSUE_UPDATE_STATE, {"id": issue_id, "stateId": target["id"]}
    )
    return updated.get("issueUpdate") or {"success": False}


async def set_delegate(issue_id: str, actor_id: str) -> dict:
    data = await _call(
        _ISSUE_UPDATE_DELEGATE, {"id": issue_id, "delegateId": actor_id}
    )
    return data.get("issueUpdate") or {"success": False}
```

In `src/eve_tools/app.py`, add the import beside the others and three entries to `_HANDLERS` before the closing brace (after `"mcp.invoke"`):

```python
    "linear.create_activity": lambda a: linear_client.create_activity(
        a["session_id"], a["content"]
    ),
    "linear.move_issue_to_started": lambda a: linear_client.move_issue_to_started(
        a["issue_id"], a["team_id"]
    ),
    "linear.set_delegate": lambda a: linear_client.set_delegate(
        a["issue_id"], a["actor_id"]
    ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_eve_tools_linear.py tests/test_eve_tools_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/linear_client.py src/eve_tools/app.py tests/test_eve_tools_linear.py
git commit -m "feat(linear): the eve-tools GraphQL client and handlers"
```

---

### Task 6: Activity builders and the non-raising emitter

**Files:**
- Create: `src/eve_linear/activities.py`
- Test: `tests/test_linear_activities.py`

**Interfaces:**
- Consumes: `eve.tools_client.invoke` (existing), `store.touch_linear_emitted` (Task 4).
- Produces: `thought(body) -> dict`, `action(action, parameter, result=None) -> dict`, `elicitation(body) -> dict`, `response(body) -> dict`, `error(body) -> dict`, all returning `{"type": ...}` content dicts; `emit(linear_session_id: str, content: dict, session_id: str | None = None) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
from eve_linear import activities


def test_each_builder_produces_the_shape_linear_validates():
    assert activities.thought("thinking") == {"type": "thought", "body": "thinking"}
    assert activities.elicitation("which repo?") == {
        "type": "elicitation",
        "body": "which repo?",
    }
    assert activities.response("done") == {"type": "response", "body": "done"}
    assert activities.error("broke") == {"type": "error", "body": "broke"}


def test_an_action_without_a_result_omits_the_result_key():
    # Linear validates these server-side; a null result on a started action
    # is a different shape from an absent one.
    assert activities.action("Searching", "the repo") == {
        "type": "action",
        "action": "Searching",
        "parameter": "the repo",
    }


def test_an_action_with_a_result_includes_it():
    assert activities.action("Searched", "the repo", "3 hits") == {
        "type": "action",
        "action": "Searched",
        "parameter": "the repo",
        "result": "3 hits",
    }


async def test_emit_calls_eve_tools_and_reports_success(monkeypatch):
    calls = []

    async def _fake_invoke(tool, arguments, **kwargs):
        calls.append((tool, arguments))
        return '{"success": true}'

    monkeypatch.setattr(activities, "invoke", _fake_invoke)

    assert await activities.emit("sess-1", activities.thought("hi")) is True
    assert calls[0][0] == "linear.create_activity"
    assert calls[0][1]["session_id"] == "sess-1"


async def test_emit_returns_false_on_an_error_string_and_never_raises(monkeypatch):
    # tools_client.invoke degrades to an "error:" string rather than raising.
    async def _fake_invoke(tool, arguments, **kwargs):
        return "error: eve-tools unavailable (ConnectError)"

    monkeypatch.setattr(activities, "invoke", _fake_invoke)

    assert await activities.emit("sess-1", activities.thought("hi")) is False


async def test_emit_swallows_an_unexpected_exception(monkeypatch):
    # THE RULE: losing narration is bad; killing a running coding session
    # because a GraphQL call failed is worse.
    async def _fake_invoke(tool, arguments, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(activities, "invoke", _fake_invoke)

    assert await activities.emit("sess-1", activities.thought("hi")) is False


async def test_emit_stamps_the_heartbeat_only_on_success(monkeypatch):
    touched = []

    async def _fake_touch(session_id):
        touched.append(session_id)

    monkeypatch.setattr(activities.store, "touch_linear_emitted", _fake_touch)

    async def _ok(tool, arguments, **kwargs):
        return '{"success": true}'

    monkeypatch.setattr(activities, "invoke", _ok)
    await activities.emit("sess-1", activities.thought("hi"), session_id="row-1")
    assert touched == ["row-1"]

    async def _fail(tool, arguments, **kwargs):
        return "error: nope"

    monkeypatch.setattr(activities, "invoke", _fail)
    await activities.emit("sess-1", activities.thought("hi"), session_id="row-1")
    # A failed emission must not advance the clock, or the heartbeat would
    # believe Linear heard something it never received.
    assert touched == ["row-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_linear_activities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_linear.activities'`.

- [ ] **Step 3: Implement**

Create `src/eve_linear/activities.py`:

```python
"""What Eve says in Linear, and the one wrapper that says it.

FIVE TYPES, VALIDATED SERVER-SIDE. Linear rejects a malformed content shape,
so the builders exist to make the shapes unmistakable at the call site rather
than assembled inline in the supervisor.

EMIT NEVER RAISES. Every caller is either the webhook's background task or
the supervisor tick, and both are driving real work. Losing the narration is
bad; failing a running coding session because a GraphQL call timed out is
worse, and it destroys work someone is waiting on. This is the same posture
`tools_client.invoke` already takes for tool calls.
"""

from __future__ import annotations

import logging

from eve.coding import store
from eve.tools_client import invoke

logger = logging.getLogger(__name__)


def thought(body: str) -> dict:
    return {"type": "thought", "body": body}


def elicitation(body: str) -> dict:
    return {"type": "elicitation", "body": body}


def response(body: str) -> dict:
    return {"type": "response", "body": body}


def error(body: str) -> dict:
    return {"type": "error", "body": body}


def action(action: str, parameter: str, result: str | None = None) -> dict:
    """`result` is omitted entirely when absent, not set to null: a started
    action and a completed one with no output are different things to
    Linear."""
    content = {"type": "action", "action": action, "parameter": parameter}
    if result is not None:
        content["result"] = result
    return content


async def emit(
    linear_session_id: str, content: dict, session_id: str | None = None
) -> bool:
    """True when Linear accepted it. `session_id` is Eve's own row id, and
    passing it stamps the heartbeat clock - only on success, so a failed
    emission does not make the heartbeat believe Linear heard from us."""
    try:
        result = await invoke(
            "linear.create_activity",
            {"session_id": linear_session_id, "content": content},
        )
    except Exception:
        logger.warning(
            "emitting a %s to linear session %s raised",
            content.get("type"),
            linear_session_id,
            exc_info=True,
        )
        return False

    if isinstance(result, str) and result.startswith("error:"):
        logger.warning(
            "could not emit a %s to linear session %s: %s",
            content.get("type"),
            linear_session_id,
            result,
        )
        return False

    if session_id:
        try:
            await store.touch_linear_emitted(session_id)
        except Exception:
            # The activity did land. Failing here would report a delivered
            # activity as lost, and the caller would retry a duplicate.
            logger.warning(
                "could not stamp the linear heartbeat for %s", session_id, exc_info=True
            )
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_linear_activities.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_linear/activities.py tests/test_linear_activities.py
git commit -m "feat(linear): activity builders and a non-raising emitter"
```

---

### Task 7: The webhook payload type and the handler

**Files:**
- Create: `src/eve_linear/types.py`, `src/eve_linear/handler.py`
- Test: `tests/test_linear_handler.py`

**Interfaces:**
- Consumes: Tasks 3, 4, 6, and `eve.coding.dispatch._recall_context`, `eve.tools_client.create_coding_session`, `eve.tools_client.prompt_coding_session`.
- Produces: `LinearEvent` (fields: `action`, `session_id`, `issue_id`, `team_id`, `actor_id`, `guidance`, `prompt_body`, `prompt_context`); `parse_event(payload: dict) -> LinearEvent`; `handle_created(event) -> str`; `handle_prompted(event) -> str`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from eve_linear import handler
from eve_linear.types import LinearEvent


@pytest.fixture(autouse=True)
def roster_and_settings(tmp_path, monkeypatch):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'noah-sub'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_noah'\n"
        "    permissions: ['code.delegate']\n"
        "  - sub: 'kendra-sub'\n"
        "    name: 'Kendra'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_kendra'\n"
        "    permissions: []\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", "s" * 32)
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


def _event(**overrides) -> LinearEvent:
    base = {
        "action": "created",
        "session_id": "lin_sess_1",
        "issue_id": "lin_issue_1",
        "team_id": "lin_team_1",
        "actor_id": "lin_noah",
        "guidance": "Work in owner/repo.",
        "prompt_body": "",
        "prompt_context": "Fix the login bug.",
    }
    base.update(overrides)
    return LinearEvent(**base)


@pytest.fixture
def spy(monkeypatch):
    """Records every outbound effect, in order, so a test can assert on
    ordering rather than only on occurrence."""
    calls = []

    async def _emit(linear_session_id, content, session_id=None):
        calls.append(("emit", content["type"]))
        return True

    async def _recall(goal, member_sub):
        calls.append(("recall", goal))
        return "remembered things"

    async def _create(session_id, agent, model, repos, goal):
        calls.append(("create_coding_session", repos))
        return "ok"

    async def _store_create(**kwargs):
        calls.append(("row", kwargs.get("linear_session_id")))

    async def _thread():
        calls.append(("thread", None))
        return "thread-1"

    async def _move(issue_id, team_id):
        calls.append(("move_issue", issue_id))
        return {"success": True}

    async def _validate(model, agent):
        # `model` is NOT NULL on the row and nobody in Linear names one, so
        # the handler always resolves the agent's fallback through here.
        return "claude-sonnet-5"

    monkeypatch.setattr(handler.activities, "emit", _emit)
    monkeypatch.setattr(handler, "_recall_context", _recall)
    monkeypatch.setattr(handler, "create_coding_session", _create)
    monkeypatch.setattr(handler.store, "create_session", _store_create)
    monkeypatch.setattr(handler, "_create_thread", _thread)
    monkeypatch.setattr(handler, "_move_issue_to_started", _move)
    monkeypatch.setattr(handler.catalogue, "validate", _validate)
    return calls


def test_parse_event_reads_the_payload_linear_actually_sends():
    payload = {
        "action": "created",
        "agentSession": {
            "id": "lin_sess_1",
            "issue": {"id": "lin_issue_1", "team": {"id": "lin_team_1"}},
            "creator": {"id": "lin_noah"},
            "guidance": "Work in owner/repo.",
            "promptContext": "Fix the login bug.",
        },
    }
    event = handler.parse_event(payload)
    assert event.action == "created"
    assert event.session_id == "lin_sess_1"
    assert event.issue_id == "lin_issue_1"
    assert event.team_id == "lin_team_1"
    assert event.actor_id == "lin_noah"
    assert "owner/repo" in event.guidance


def test_parse_event_survives_a_payload_with_no_issue():
    # A mention in a document has no issue. Eve refuses it later, but parsing
    # must not raise, or the 202 path turns into a 500 and Linear retries.
    event = handler.parse_event({"action": "created", "agentSession": {"id": "s"}})
    assert event.issue_id is None


async def test_the_acknowledgement_is_emitted_before_recall(spy):
    # THE CANARY. This ordering is the entire 10-second contract, and it is
    # invisible in the code once written. A refactor that hoists a database
    # read above the emission breaks the contract while every other test
    # still passes.
    await handler.handle_created(_event())

    kinds = [c[0] for c in spy]
    assert kinds.index("emit") < kinds.index("recall")
    assert spy[0] == ("emit", "thought")


async def test_an_accepted_delegation_dispatches_and_writes_the_row(spy):
    result = await handler.handle_created(_event())
    assert result == "dispatched"
    assert ("create_coding_session", ["owner/repo"]) in spy
    assert ("row", "lin_sess_1") in spy


async def test_an_unmapped_linear_user_is_refused_with_an_error(spy):
    result = await handler.handle_created(_event(actor_id="lin_stranger"))
    assert result == "refused:unmapped"
    assert spy[0] == ("emit", "error")
    assert not any(c[0] == "recall" for c in spy)
    assert not any(c[0] == "create_coding_session" for c in spy)


async def test_a_member_without_the_permission_is_refused_with_an_error(spy):
    result = await handler.handle_created(_event(actor_id="lin_kendra"))
    assert result == "refused:unpermitted"
    assert spy[0] == ("emit", "error")
    assert not any(c[0] == "create_coding_session" for c in spy)


async def test_guidance_outside_the_allowlist_is_refused_with_an_elicitation(spy):
    result = await handler.handle_created(_event(guidance="Use evil/backdoor."))
    assert result == "refused:no_repo"
    assert spy[0] == ("emit", "elicitation")
    assert not any(c[0] == "create_coding_session" for c in spy)


async def test_a_dispatch_failure_after_the_acknowledgement_emits_an_error(
    spy, monkeypatch
):
    async def _failing_create(session_id, agent, model, repos, goal):
        return "error: eve-computer unavailable (ConnectError)"

    monkeypatch.setattr(handler, "create_coding_session", _failing_create)

    result = await handler.handle_created(_event())

    assert result == "failed"
    assert [c[1] for c in spy if c[0] == "emit"] == ["thought", "error"]
    # No row, or the supervisor polls forever for work that never started.
    assert not any(c[0] == "row" for c in spy)


async def test_a_prompted_event_resumes_a_blocked_session(monkeypatch):
    sent = []

    async def _get(linear_session_id):
        return {"id": "row-1", "status": "blocked", "linear_session_id": linear_session_id}

    async def _prompt(session_id, text, kind="reply"):
        sent.append((session_id, text, kind))
        return "ok"

    async def _set_status(session_id, status):
        sent.append(("status", session_id, status))

    monkeypatch.setattr(handler.store, "get_by_linear_session", _get)
    monkeypatch.setattr(handler.store, "set_status", _set_status)
    monkeypatch.setattr(handler, "prompt_coding_session", _prompt)

    result = await handler.handle_prompted(
        _event(action="prompted", prompt_body="Use the staging database.")
    )

    assert result == "resumed"
    assert ("row-1", "Use the staging database.", "interjection") in sent
    assert ("status", "row-1", "running") in sent


async def test_a_prompted_event_for_a_finished_session_says_so(monkeypatch):
    emitted = []

    async def _get(linear_session_id):
        return {"id": "row-1", "status": "finished"}

    async def _emit(linear_session_id, content, session_id=None):
        emitted.append(content["type"])
        return True

    monkeypatch.setattr(handler.store, "get_by_linear_session", _get)
    monkeypatch.setattr(handler.activities, "emit", _emit)

    result = await handler.handle_prompted(_event(action="prompted", prompt_body="hi"))
    assert result == "already-resolved"
    assert emitted == ["error"]


async def test_a_prompted_event_for_an_unknown_session_is_reported(monkeypatch):
    async def _get(linear_session_id):
        return None

    monkeypatch.setattr(handler.store, "get_by_linear_session", _get)

    result = await handler.handle_prompted(_event(action="prompted", prompt_body="hi"))
    assert result == "unknown-session"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_linear_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_linear.types'`.

- [ ] **Step 3: Implement**

Create `src/eve_linear/types.py`:

```python
"""The parsed webhook event.

A dataclass rather than a pydantic model: nothing here is a structured-output
schema for a model, and every field is optional in practice because Linear
sends agent sessions for surfaces this feature does not support (documents,
projects). Parsing must never raise - a 500 on the webhook path tells Linear
to retry a request that will fail the same way forever.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinearEvent:
    action: str
    session_id: str | None
    issue_id: str | None
    team_id: str | None
    actor_id: str | None
    guidance: str
    prompt_body: str
    prompt_context: str
```

Create `src/eve_linear/handler.py`:

```python
"""Acknowledge, gate, dispatch. The ordering here IS the contract.

THE TEN SECOND RULE. Linear marks a session unresponsive if no activity
arrives within ten seconds of `created`. A dispatch does a hybrid memory
recall and an HTTP call to eve-computer, so neither may precede the first
emission. The three gate checks DO precede it, because all three are pure
local computation (a dict lookup, a frozenset test, a string scan) and their
result decides which activity to emit. So the acknowledgement is always
exactly one GraphQL call, and nothing that can block on a network or a
database runs before it.

`tests/test_linear_handler.py` asserts that ordering directly, because it is
invisible in the code once written and a well-meaning refactor will break it.

WHY THE ROW IS WRITTEN AFTER THE BOX ACCEPTS. Same reason
`eve.coding.dispatch` does it: a row for a session the box never heard of is
polled forever by the supervisor and eventually reported as stale, for work
that never started.
"""

from __future__ import annotations

import logging
import uuid

from eve.coding import catalogue, store
from eve.coding.dispatch import _recall_context
from eve.settings import get_settings
from eve.tools_client import create_coding_session, invoke, prompt_coding_session
from eve_linear import activities
from eve_linear.identity import resolve_member, resolve_repos
from eve_linear.types import LinearEvent

logger = logging.getLogger(__name__)

_ASSISTANT = "eve"


def parse_event(payload: dict) -> LinearEvent:
    """Total over every payload shape Linear sends. Absent nesting becomes
    None rather than a KeyError, because a mention in a document is a real
    event this feature refuses rather than crashes on."""
    session = payload.get("agentSession") or {}
    issue = session.get("issue") or {}
    team = issue.get("team") or {}
    creator = session.get("creator") or session.get("actor") or {}
    activity = payload.get("agentActivity") or {}
    content = activity.get("content") or {}
    return LinearEvent(
        action=payload.get("action") or "",
        session_id=session.get("id"),
        issue_id=issue.get("id"),
        team_id=team.get("id"),
        actor_id=creator.get("id"),
        guidance=session.get("guidance") or "",
        prompt_body=content.get("body") or "",
        prompt_context=session.get("promptContext") or "",
    )


async def _create_thread() -> str | None:
    """A Linear session gets an Aegra thread too, so the family still gets
    the push notification and a thread to talk in when it resolves. Isolated
    into its own function so the handler's tests can replace it without an
    Aegra client."""
    from langgraph_sdk import get_client

    settings = get_settings()
    client = get_client(
        url=settings.ambient_aegra_base_url,
        headers={"Authorization": f"Bearer {settings.ambient_token}"},
    )
    thread = await client.threads.create(metadata={"linear": True})
    return thread["thread_id"]


async def _move_issue_to_started(issue_id: str, team_id: str) -> dict:
    result = await invoke(
        "linear.move_issue_to_started", {"issue_id": issue_id, "team_id": team_id}
    )
    return {"success": not (isinstance(result, str) and result.startswith("error:"))}


async def handle_created(event: LinearEvent) -> str:
    settings = get_settings()

    member, refusal = resolve_member(event.actor_id or "")
    repos: list[str] = []
    if refusal is None:
        repos, refusal = resolve_repos(event.guidance, settings.linear_repo_allowlist)

    if refusal is not None:
        # An elicitation for the answerable refusal, an error for the two
        # that no reply in Linear can fix.
        content = (
            activities.elicitation(refusal.message)
            if refusal.kind == "no_repo"
            else activities.error(refusal.message)
        )
        await activities.emit(event.session_id, content)
        return f"refused:{refusal.kind}"

    goal = event.prompt_context or event.prompt_body
    await activities.emit(
        event.session_id,
        activities.thought(f"Picking this up now. Working in {', '.join(repos)}."),
    )

    context = await _recall_context(goal, member.sub)
    thread_id = await _create_thread()
    if not thread_id:
        await activities.emit(
            event.session_id,
            activities.error("I couldn't open a thread to track this. Nothing started."),
        )
        return "failed"

    session_id = str(uuid.uuid4())
    agent = settings.coding_default_agent
    # `model` is NOT NULL on the row, and nobody in Linear named one.
    # `catalogue.validate(None, agent)` resolves the agent's fallback rather
    # than raising, which is exactly this case.
    model = await catalogue.validate(None, agent)
    dispatched = await create_coding_session(
        session_id, agent, model, repos, goal
    )
    if isinstance(dispatched, str) and dispatched.startswith("error:"):
        await activities.emit(
            event.session_id,
            activities.error(f"I couldn't start the coding session: {dispatched}"),
        )
        return "failed"

    await store.create_session(
        session_id=session_id,
        member_sub=member.sub,
        thread_id=thread_id,
        goal=goal,
        agent=agent,
        model=model,
        repos=repos,
        context=context,
        linear_session_id=event.session_id,
        linear_issue_id=event.issue_id,
    )

    if event.issue_id and event.team_id:
        # Best-effort, and deliberately after the row exists: the work is
        # already underway, and a board that did not move is cosmetic.
        await _move_issue_to_started(event.issue_id, event.team_id)

    return "dispatched"


async def handle_prompted(event: LinearEvent) -> str:
    """A human answering an elicitation, or interjecting mid-run.

    `escalate` parks a session rather than resolving it precisely so this can
    resume the same one, with its subprocess and worktrees intact.
    """
    row = await store.get_by_linear_session(event.session_id or "")
    if row is None:
        logger.info("prompted for an unknown linear session %s", event.session_id)
        return "unknown-session"

    if row["status"] in ("finished", "failed", "stale"):
        await activities.emit(
            event.session_id,
            activities.error(
                "That session has already finished. Delegate the issue again "
                "and I'll start a fresh one."
            ),
            session_id=row["id"],
        )
        return "already-resolved"

    sent = await prompt_coding_session(
        row["id"], event.prompt_body, kind="interjection"
    )
    if isinstance(sent, str) and sent.startswith("error:"):
        await activities.emit(
            event.session_id,
            activities.error("I couldn't pass that on to the coding agent."),
            session_id=row["id"],
        )
        return "failed"

    if row["status"] == "blocked":
        await store.set_status(row["id"], "running")
    return "resumed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_linear_handler.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_linear/types.py src/eve_linear/handler.py tests/test_linear_handler.py
git commit -m "feat(linear): the webhook handler and its ten-second ordering"
```

---

### Task 8: The endpoint

**Files:**
- Modify: `src/eve_ambient/app.py` (imports at 16-25; constants near 44; new endpoint after `home_assistant_signal`, which ends at line 311)
- Test: `tests/test_ambient_app.py`

**Interfaces:**
- Consumes: Tasks 2 and 7.
- Produces: `POST /signals/linear`; module state `_linear_in_flight: set[str]`, `_linear_semaphore`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ambient_app.py`:

```python
import hashlib
import hmac
import json
import time

LINEAR_SECRET = "L" * 32


@pytest.fixture
def linear_settings(monkeypatch):
    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", LINEAR_SECRET)
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _linear_post(client, payload, secret=LINEAR_SECRET):
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/signals/linear",
        content=body,
        headers={"linear-signature": signature, "content-type": "application/json"},
    )


def _created_payload(**overrides):
    payload = {
        "action": "created",
        "webhookTimestamp": int(time.time() * 1000),
        "agentSession": {
            "id": "lin_sess_1",
            "issue": {"id": "lin_issue_1", "team": {"id": "lin_team_1"}},
            "creator": {"id": "lin_noah"},
            "guidance": "Work in owner/repo.",
            "promptContext": "Fix the bug.",
        },
    }
    payload.update(overrides)
    return payload


def test_linear_webhook_accepts_a_correctly_signed_created(
    client, linear_settings, monkeypatch
):
    handled = []

    async def _fake_created(event):
        handled.append(event.session_id)
        return "dispatched"

    monkeypatch.setattr(app_module.linear_handler, "handle_created", _fake_created)

    response = _linear_post(client, _created_payload())
    assert response.status_code == 202
    assert response.json() == {"accepted": "lin_sess_1"}


def test_linear_webhook_refuses_a_bad_signature(client, linear_settings):
    response = _linear_post(client, _created_payload(), secret="wrong" * 8)
    assert response.status_code == 401


def test_linear_webhook_refuses_a_stale_timestamp(client, linear_settings):
    stale = _created_payload(webhookTimestamp=int(time.time() * 1000) - 120_000)
    response = _linear_post(client, stale)
    assert response.status_code == 401


def test_linear_webhook_answers_503_when_disabled(client, monkeypatch):
    monkeypatch.setenv("EVE_LINEAR_ENABLED", "false")
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", LINEAR_SECRET)
    from eve.settings import get_settings

    get_settings.cache_clear()

    response = _linear_post(client, _created_payload())
    # The endpoint exists; the subsystem behind it is switched off.
    assert response.status_code == 503


def test_linear_webhook_dedups_a_concurrent_duplicate(
    client, linear_settings, monkeypatch
):
    calls = []

    async def _slow_created(event):
        calls.append(event.session_id)
        await asyncio.sleep(0.2)
        return "dispatched"

    monkeypatch.setattr(app_module.linear_handler, "handle_created", _slow_created)

    first = _linear_post(client, _created_payload())
    second = _linear_post(client, _created_payload())
    assert first.status_code == 202
    assert second.status_code == 202
    # The unique index is the durable guard; this one just avoids the wasted
    # round trip while the first is still in flight.
    assert len(calls) <= 1


def test_linear_webhook_refuses_an_unusable_payload(client, linear_settings):
    response = _linear_post(client, {"webhookTimestamp": int(time.time() * 1000)})
    assert response.status_code == 422


def test_linear_webhook_ignores_an_action_it_does_not_handle(
    client, linear_settings, monkeypatch
):
    called = []

    async def _fake_created(event):
        called.append(event.session_id)
        return "dispatched"

    monkeypatch.setattr(app_module.linear_handler, "handle_created", _fake_created)

    response = _linear_post(client, _created_payload(action="somethingElse"))
    assert response.status_code == 202
    assert called == []
```

Add `app_module._linear_in_flight.clear()` to the existing `_clear_background_tasks` fixture, both before and after the `yield`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ambient_app.py -k linear -v`
Expected: FAIL with `AttributeError: module 'eve_ambient.app' has no attribute 'linear_handler'`.

- [ ] **Step 3: Implement**

In `src/eve_ambient/app.py`, add to the imports:

```python
from eve_linear import handler as linear_handler
from eve_linear.verify import timestamp_is_fresh, verify_signature
```

After `_webhook_semaphore` (line 45):

```python
# Linear-originated work gets its own in-flight set and semaphore rather than
# sharing the ambient ones: a burst of delegations must not starve the Home
# Assistant path, and the two have different natural concurrencies.
_linear_in_flight: set[str] = set()
_linear_semaphore = asyncio.Semaphore(3)
```

After `home_assistant_signal` (line 311):

```python
@app.post("/signals/linear", status_code=202)
async def linear_signal(request: Request) -> dict:
    """Linear wants a response within 5 seconds and a first activity within
    10. Everything on this path is local computation; the dispatch, which is
    neither, runs in a background task.

    The raw body is read before parsing, because the signature covers the
    exact bytes Linear sent and re-serializing parsed JSON changes them.
    """
    raw = await request.body()
    settings = get_settings()
    if not verify_signature(
        raw, request.headers.get("linear-signature"), settings.linear_webhook_secret
    ):
        # The presented signature is deliberately not logged.
        logger.warning("rejected linear webhook: invalid or missing signature")
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unusable payload: {exc}") from exc

    if not timestamp_is_fresh(payload.get("webhookTimestamp")):
        # A replayed capture, or a clock we cannot reason about. 401 rather
        # than 422: this is an authentication failure, not a shape problem.
        logger.warning("rejected linear webhook: stale or missing timestamp")
        raise HTTPException(status_code=401, detail="unauthorized")

    event = linear_handler.parse_event(payload)
    if not event.session_id:
        raise HTTPException(status_code=422, detail="no agent session in payload")

    if not settings.linear_enabled:
        # Same lever, same position, same reason as the ambient check above.
        raise HTTPException(status_code=503, detail="linear is disabled")

    if event.action not in ("created", "prompted"):
        # Acknowledged and ignored. A 4xx would make Linear retry an event
        # this feature will never handle.
        logger.info("ignoring linear action %r", event.action)
        return {"accepted": event.session_id}

    if event.session_id in _linear_in_flight:
        logger.info(
            "linear session %s is already in flight; not queuing a duplicate",
            event.session_id,
        )
        return {"accepted": event.session_id}
    _linear_in_flight.add(event.session_id)

    task = asyncio.create_task(_handle_linear_in_background(event))
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(
        lambda _task, key=event.session_id: _linear_in_flight.discard(key)
    )
    return {"accepted": event.session_id}


async def _handle_linear_in_background(event) -> None:
    try:
        async with _linear_semaphore:
            if event.action == "created":
                outcome = await linear_handler.handle_created(event)
            else:
                outcome = await linear_handler.handle_prompted(event)
            logger.info("linear session %s resolved as %s", event.session_id, outcome)
    except Exception:
        logger.warning("linear session %s failed", event.session_id, exc_info=True)
```

Add `import json` to the imports at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ambient_app.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/app.py tests/test_ambient_app.py
git commit -m "feat(linear): the /signals/linear endpoint"
```

---

### Task 9: Supervisor emissions and the heartbeat

**Files:**
- Modify: `src/eve/coding/supervisor.py:120-191` (`_advance`)
- Test: `tests/test_coding_supervisor.py`

**Interfaces:**
- Consumes: Tasks 4 and 6.
- Produces: `supervisor._emit_for(row, content) -> None`; `supervisor._heartbeat(row, box, now, settings) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coding_supervisor.py`, following that file's existing fake-row and monkeypatch conventions:

```python
@pytest.fixture
def emitted(monkeypatch):
    calls = []

    async def _emit(linear_session_id, content, session_id=None):
        calls.append((linear_session_id, content["type"]))
        return True

    monkeypatch.setattr(supervisor.activities, "emit", _emit)
    return calls


async def test_a_reply_emits_an_action(emitted, monkeypatch):
    row = _row(status="running", linear_session_id="lin_sess_1")
    _patch_box(monkeypatch, {"status": "idle", "turns": [{"role": "agent", "text": "?"}]})
    _patch_decision(monkeypatch, action="reply", text="Use the staging database.")

    await supervisor._advance(row, _now(), _stale_after(), get_settings())

    assert ("lin_sess_1", "action") in emitted


async def test_an_escalation_emits_an_elicitation(emitted, monkeypatch):
    row = _row(status="running", linear_session_id="lin_sess_1")
    _patch_box(monkeypatch, {"status": "idle", "turns": [{"role": "agent", "text": "?"}]})
    _patch_decision(monkeypatch, action="escalate", text="Which database?")

    await supervisor._advance(row, _now(), _stale_after(), get_settings())

    assert ("lin_sess_1", "elicitation") in emitted


async def test_finishing_emits_a_response_carrying_the_pull_requests(
    emitted, monkeypatch
):
    row = _row(status="running", linear_session_id="lin_sess_1")
    _patch_box(monkeypatch, {"status": "idle", "turns": [{"role": "agent", "text": "done"}]})
    _patch_decision(monkeypatch, action="done", text="Fixed the login bug.")
    _patch_close(monkeypatch, {"prs": [{"repo": "owner/repo", "pr_url": "https://pr/1"}]})

    await supervisor._advance(row, _now(), _stale_after(), get_settings())

    assert ("lin_sess_1", "response") in emitted


async def test_a_failure_emits_an_error(emitted, monkeypatch):
    row = _row(status="running", linear_session_id="lin_sess_1")
    _patch_box(monkeypatch, {"status": "failed", "error": "the agent crashed"})

    await supervisor._advance(row, _now(), _stale_after(), get_settings())

    assert ("lin_sess_1", "error") in emitted


async def test_a_chat_dispatched_session_emits_nothing(emitted, monkeypatch):
    # The overwhelmingly common case. A session with no Linear side must not
    # cost a single call.
    row = _row(status="running", linear_session_id=None)
    _patch_box(monkeypatch, {"status": "idle", "turns": [{"role": "agent", "text": "?"}]})
    _patch_decision(monkeypatch, action="reply", text="Carry on.")

    await supervisor._advance(row, _now(), _stale_after(), get_settings())

    assert emitted == []


async def test_an_emission_failure_does_not_stop_the_session(monkeypatch):
    async def _raising_emit(linear_session_id, content, session_id=None):
        raise RuntimeError("linear is down")

    monkeypatch.setattr(supervisor.activities, "emit", _raising_emit)
    row = _row(status="running", linear_session_id="lin_sess_1")
    _patch_box(monkeypatch, {"status": "idle", "turns": [{"role": "agent", "text": "done"}]})
    _patch_decision(monkeypatch, action="done", text="Done.")
    _patch_close(monkeypatch, {"prs": []})

    # The session still resolves. Losing narration must never destroy work.
    outcome = await supervisor._advance(row, _now(), _stale_after(), get_settings())
    assert outcome["status"] == "finished"


async def test_the_heartbeat_fires_after_the_threshold(emitted, monkeypatch):
    now = _now()
    stale_stamp = now - timedelta(minutes=11)
    row = _row(
        status="running", linear_session_id="lin_sess_1", linear_emitted_at=stale_stamp
    )
    # `running`, not `idle`: there is no decision to make, which is exactly
    # when a long silence would otherwise strand the Linear session.
    _patch_box(monkeypatch, {"status": "running", "activity": ["compiling"]})

    await supervisor._advance(row, now, _stale_after(), get_settings())

    assert ("lin_sess_1", "thought") in emitted


async def test_the_heartbeat_does_not_fire_before_the_threshold(emitted, monkeypatch):
    now = _now()
    row = _row(
        status="running",
        linear_session_id="lin_sess_1",
        linear_emitted_at=now - timedelta(minutes=2),
    )
    _patch_box(monkeypatch, {"status": "running", "activity": ["compiling"]})

    await supervisor._advance(row, now, _stale_after(), get_settings())

    assert emitted == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coding_supervisor.py -k "emit or heartbeat" -v`
Expected: FAIL with `AttributeError: module 'eve.coding.supervisor' has no attribute 'activities'`.

- [ ] **Step 3: Implement**

In `src/eve/coding/supervisor.py`, add the import:

```python
from eve_linear import activities
```

Add the two helpers after `_resolved` (line 117):

```python
async def _emit_for(row: dict, content: dict) -> None:
    """A no-op for the common case: a chat-dispatched session has no Linear
    side and must not cost a call. Never raises - losing the narration is
    survivable, failing a running coding session is not."""
    linear_session_id = row.get("linear_session_id")
    if not linear_session_id:
        return
    try:
        await activities.emit(linear_session_id, content, session_id=row["id"])
    except Exception:
        logger.warning(
            "emitting to linear for session %s raised", row["id"], exc_info=True
        )


async def _heartbeat(row: dict, box: dict, now, settings) -> None:
    """Linear marks a session stale after 30 minutes of silence, and a coding
    agent can work far longer than that without producing a decision. The
    state is recoverable (a later activity un-stales it), so this is about
    not inviting a human to intervene in work that is going fine."""
    if not row.get("linear_session_id"):
        return
    last = row.get("linear_emitted_at") or row.get("created_at")
    if last is None:
        return
    if (now - last) < timedelta(minutes=settings.linear_heartbeat_minutes):
        return
    latest = "; ".join((box.get("activity") or [])[-1:]) or "still working"
    await _emit_for(row, activities.thought(f"Still on it: {latest}"))
```

In `_advance`, add the emissions. After the timeout branch's `mark_resolved` (line 129):

```python
        await _emit_for(row, activities.error(result["error"]))
```

After the `status == "failed"` branch's `mark_resolved` (line 143):

```python
        await _emit_for(row, activities.error(result["error"]))
```

After the `status == "killed"` branch's `mark_resolved` (line 146):

```python
        await _emit_for(row, activities.error("the session was killed"))
```

Replace the `if status != "idle": return None` line (line 148-149) with:

```python
    if status != "idle":
        # The only place a session is doing work with no decision to make,
        # which is exactly where a long Linear silence would strand it.
        await _heartbeat(row, box, now, settings)
        return None
```

In the `reply` branch, after `set_status(..., "running")` (line 181):

```python
        await _emit_for(
            row, activities.action("Working", row["goal"], decision.text)
        )
```

In the `escalate` branch, after `set_status(..., "blocked")` (line 185):

```python
        await _emit_for(row, activities.elicitation(decision.text))
```

In the final `done` path, after `mark_resolved(..., "finished", result)` (line 190):

```python
    prs = [pr for pr in result.get("prs", []) if pr.get("pr_url")]
    links = "; ".join(f"{pr['repo']}: {pr['pr_url']}" for pr in prs)
    await _emit_for(
        row,
        activities.response(
            f"{decision.text} Pull requests: {links}" if links else
            f"{decision.text} No changes, so there's no pull request."
        ),
    )
```

Also add the `stale` emission in the `box is None` branch, after `mark_resolved(..., "stale", {})` (line 136):

```python
            await _emit_for(
                row, activities.error("the session went quiet and never reported back")
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coding_supervisor.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add src/eve/coding/supervisor.py tests/test_coding_supervisor.py
git commit -m "feat(linear): supervisor emissions and the stale-session heartbeat"
```

---

### Task 10: Integration test, documentation, and the ADR amendment

**Files:**
- Create: `tests/test_linear_integration.py`
- Modify: `docs/architecture.md`, `docs/adr/0006-eve-tools-isolation.md`
- Test: the new integration file

**Interfaces:**
- Consumes: every prior task.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

```python
"""End to end with eve-computer and Linear both faked.

Asserts the seams the unit tests cannot: that the ordering contract holds
through the real handler, that a Linear retry dispatches exactly once, and
that an elicitation answered in Linear resumes the same session.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module

SECRET = "L" * 32


def _post(client, payload):
    body = json.dumps(payload).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/signals/linear",
        content=body,
        headers={"linear-signature": signature, "content-type": "application/json"},
    )


def _created(session_id="lin_sess_1"):
    return {
        "action": "created",
        "webhookTimestamp": int(time.time() * 1000),
        "agentSession": {
            "id": session_id,
            "issue": {"id": "lin_issue_1", "team": {"id": "lin_team_1"}},
            "creator": {"id": "lin_noah"},
            "guidance": "Work in owner/repo.",
            "promptContext": "Fix the login bug.",
        },
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Fakes everything outside this repository: Linear's API, eve-computer,
    Aegra, and memory recall. The database is real."""
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'noah-sub'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_noah'\n"
        "    permissions: ['code.delegate']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')

    from eve.family import get_family
    from eve.settings import get_settings
    from eve_linear import activities, handler

    get_settings.cache_clear()
    get_family.cache_clear()

    state = {"activities": [], "dispatches": [], "order": []}

    async def _fake_invoke(tool, arguments, **kwargs):
        if tool == "linear.create_activity":
            state["activities"].append(arguments["content"])
            state["order"].append("emit")
            return '{"success": true}'
        return '{"success": true}'

    async def _fake_create(session_id, agent, model, repos, goal):
        state["dispatches"].append(session_id)
        return "ok"

    async def _fake_recall(goal, member_sub):
        state["order"].append("recall")
        return ""

    async def _fake_thread():
        return "thread-1"

    async def _fake_validate(model, agent):
        return "claude-sonnet-5"

    monkeypatch.setattr(activities, "invoke", _fake_invoke)
    monkeypatch.setattr(handler, "invoke", _fake_invoke)
    monkeypatch.setattr(handler, "create_coding_session", _fake_create)
    monkeypatch.setattr(handler, "_recall_context", _fake_recall)
    monkeypatch.setattr(handler, "_create_thread", _fake_thread)
    monkeypatch.setattr(handler.catalogue, "validate", _fake_validate)

    yield state

    get_settings.cache_clear()
    get_family.cache_clear()


@pytest.fixture
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_a_delegation_acknowledges_then_dispatches(client, world):
    response = _post(client, _created())
    assert response.status_code == 202

    # Give the background task a moment to run.
    time.sleep(0.5)

    assert world["activities"][0]["type"] == "thought"
    # The ordering contract, through the real handler this time.
    assert world["order"].index("emit") < world["order"].index("recall")
    assert len(world["dispatches"]) == 1


def test_a_retried_created_dispatches_exactly_once(client, world):
    _post(client, _created())
    time.sleep(0.5)
    _post(client, _created())
    time.sleep(0.5)

    # The unique index refuses the second row, so the second dispatch does
    # not produce a second supervised session.
    assert len(world["dispatches"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_linear_integration.py -v`
Expected: FAIL (the second test fails until the duplicate insert is handled, and the first fails if any wiring is off).

- [ ] **Step 3: Make it pass, then write the docs**

In `src/eve_linear/handler.py`, wrap the `store.create_session` call so a duplicate Linear session id is a refusal rather than a traceback:

```python
    try:
        await store.create_session(
            session_id=session_id,
            member_sub=member.sub,
            thread_id=thread_id,
            goal=goal,
            agent=agent,
            model=model,
            repos=repos,
            context=context,
            linear_session_id=event.session_id,
            linear_issue_id=event.issue_id,
        )
    except Exception:
        # Almost certainly the unique index on linear_session_id: Linear
        # retried, and another attempt already owns this agent session. The
        # box is now running a session with no row, so stop it rather than
        # leaving an orphan burning tokens.
        logger.info(
            "linear session %s already has a coding session; not duplicating",
            event.session_id,
        )
        await kill_coding_session(session_id)
        return "duplicate"
```

Add `kill_coding_session` to the `eve.tools_client` import in that file.

In `docs/architecture.md`, add a `## Linear` section after `## Ambient`, describing: the endpoint and its verification, the credential split (signing secret in `eve-ambient`, API token in `eve-tools`), the decision-to-activity mapping, the allowlist as the injection boundary, the heartbeat, and links to this plan and the spec. In the `## Ambient` section, add a sentence noting that `/signals/linear` shares the webhook posture but deliberately bypasses the gate chain, because quiet hours and the daily cap are built to protect the family from unrequested interruptions and would misfire on explicitly requested work.

In `docs/adr/0006-eve-tools-isolation.md`, append to Consequences:

```markdown
**Amendment (EVE-26, 2026-09-21): inbound-initiated third parties.** Linear
is the first third party that initiates contact with Eve rather than being
polled, which raises the question of whether its webhook receiver must live
in `eve-tools`. It does not, and the rule is unchanged: verification and
action are separable. The webhook signing secret proves Linear reached us and
grants no authority over the workspace, so it lives in `eve-ambient` beside
the Home Assistant secret. The OAuth token reaches Linear, so it lives here,
behind the `linear.*` handlers, exactly like every other credential.
```

- [ ] **Step 4: Run the full suite**

Run: `pytest`
Expected: PASS. No pre-existing test may regress; `test_alembic_graph.py` must still report a single head.

- [ ] **Step 5: Commit**

```bash
git add tests/test_linear_integration.py src/eve_linear/handler.py docs/architecture.md docs/adr/0006-eve-tools-isolation.md
git commit -m "feat(linear): integration coverage, architecture notes, and the ADR 0006 amendment"
```

---

## Self-Review

**Spec coverage.** Every section maps to a task: the two deadlines to Tasks 7 and 8; identity and authority to Tasks 1 and 3; the allowlist to Task 3; the data model to Task 4; the credential split to Task 5; the lifecycle mapping, heartbeat, and issue status to Tasks 5 and 9; the return path to Task 7; failure modes across 5, 6, 7, and 9; settings to Task 1; testing throughout, with the ordering canary in Tasks 7 and 10; documentation to Task 10.

**Two spec items deliberately reshaped during planning**, both noted here so a reviewer can object:

1. The spec says the acknowledgement precedes the family lookup. It cannot: the lookup's result is what decides whether to emit a `thought` or an `error`. The spec was corrected in the same commit series to say the gates run first because they are pure local computation with no IO, which preserves the ten second guarantee. Task 7's docstring and its canary test encode the corrected rule.
2. The spec does not say what happens when the unique constraint fires after `eve-computer` has already accepted. Task 10 kills the orphaned box session, because the alternative is an unsupervised agent burning tokens with no row to resolve it.
3. `eve_coding_session.model` is `NOT NULL` (migration 0005), and nobody delegating from Linear names a model. Task 7 resolves one through `catalogue.validate(None, agent)`, which exists for exactly this case and falls back rather than raising. Caught during planning by reading the schema rather than trusting the spec's silence.

**Import direction.** `eve_linear` imports from `eve.*`; the only reverse edge is `supervisor` importing `eve_linear.activities`, which reaches `eve.coding.store` and `eve.tools_client` and nothing that returns to `eve_linear`. Verified against the current import graph, and worth re-checking if Task 6 ever grows a dependency.

**Placeholder scan.** No TBDs, no "add error handling", no "similar to Task N". Every code step carries the code.

**Type consistency.** `linear_session_id` is the Linear agent session id everywhere; `session_id` is Eve's own row id everywhere, except in `activities.emit` and the `linear.create_activity` tool arguments, where `session_id` is Linear's (it crosses to Linear's API, which names it that) and Eve's row id is the explicitly-named `session_id=` keyword. That collision is real and worth watching in review: Task 6's signature is `emit(linear_session_id, content, session_id=None)`, and the second is Eve's. `Refusal.kind` values are `"unmapped" | "unpermitted" | "no_repo"` in Tasks 3 and 7. `Member.linear_id` is `str | None` in Tasks 1 and 3. `store.create_session`'s new keywords match between Tasks 4, 7, and 10.
