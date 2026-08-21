# Eve Phase 3 (Specialists + Skills) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `eve` in a tool-calling loop so she can invoke three domain
specialists (Home, Mail, Finances) and discover new capabilities at runtime
(SKILL.md procedures, dynamically-bound MCP tools), with every third-party
credential and API call isolated in a separate `eve-tools` service.

**Architecture:** `eve`'s graph gains a conditional `eve <-> tools` cycle
(LangGraph's `ToolNode` / `tools_condition`). Specialists are
`langchain.agents.create_agent` sub-agents built by one shared factory,
exposed to `eve` as single opaque tools (`ask_home`, `ask_mail`,
`ask_finances`). Every specialist's leaf tool, plus the generic MCP
dispatcher used by dynamically-discovered skills, is a thin HTTP call to
`eve-tools` — a new, separate service/package (`src/eve_tools/`) holding
every third-party credential (Home Assistant, Gmail, Monarch Money), with no
family data and no cluster credentials of its own.

**Tech Stack:** `langchain.agents.create_agent` (not the deprecated
`langgraph.prebuilt.create_react_agent`), `langgraph.prebuilt.ToolNode` /
`tools_condition` / `InjectedState`, `langchain_core.tools.InjectedToolCallId`,
`fastapi` + `uvicorn` (already transitive via `aegra-api`, added as direct
dependencies for `eve-tools`), `mcp` (official Python SDK, generic MCP
client), `google-api-python-client` + `google-auth-oauthlib` (Gmail),
`monarchmoney` (Monarch Money — no official API; this is the
community-maintained client), `httpx` (promoted from dev-only to a runtime
dependency for the `eve-tools` HTTP client).

**Spec:** [`docs/superpowers/specs/2026-08-21-eve-specialists-design.md`](../specs/2026-08-21-eve-specialists-design.md)

## Global Constraints

- No model call may precede the first streamed token (ADR 0002). Nothing in
  this plan touches `load_context` or `recall`; the tools loop lives entirely
  after `eve`'s first model call.
- `models.py` is the only place model identifiers appear (ADR 0004). Every
  new model call in this plan goes through `eve.models.get_model(tier)`.
- Specialists run on `Tier.MECHANICAL` (`chatgpt/gpt-5.6-luna`); Eve's own
  loop stays on `Tier.VOICE` (`chatgpt/gpt-5.6-terra`). Neither tier mapping
  changes in this plan.
- `EveState.dynamic_tools` holds only JSON-serializable `DynamicToolSpec`
  values — never a live callable, connection, or session object. Aegra
  checkpoints `EveState` to Postgres on every turn.
- `eve-tools` holds every third-party credential and has no family,
  permission, or Kubernetes data. All specialist and MCP tool execution goes
  through it via one route, `POST /invoke`.
- Permission checks happen in `eve`'s main container, before any HTTP call to
  `eve-tools`, and return a plain string to the model on denial — never an
  exception. `family.yaml`'s `finances` permission (renamed from `spend`
  during design) is already committed.
- Every new async function that calls an external system (Home Assistant,
  Gmail, Monarch Money, `eve-tools` itself) must degrade to a returned error
  string on failure, not a raised exception, matching how `memory/recall.py`
  and `memory/extract.py` already degrade.
- Test tiers match the existing convention exactly: unit (no network, default
  run), `@pytest.mark.integration` (needs `docker-compose.test.yml` and any
  locally-spawned server processes), `@pytest.mark.live` (needs
  `EVE_LIVE_TESTS=1` and real credentials).

---

## Task 1: Dependencies and settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/eve/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings.tools_base_url: str`, `Settings.tools_api_key: str`,
  `Settings.skills_dir: Path`, `Settings.specialist_max_iterations: int`,
  `Settings.dynamic_tools_cap: int` — consumed by every later task.

- [ ] **Step 1: Add the new dependencies**

Verified against this project's actual lockfile with `uv pip install --dry-run`
before writing this plan: adding all six resolves cleanly against the existing
tree, with `langchain-core` bumping `1.5.5 -> 1.6.0` (pulled in by `langchain`)
as the only version change to an existing package. `fastapi` and `uvicorn` are
already present transitively via `aegra-api`; this makes Eve's own direct use
of them in `eve-tools` explicit instead of implicit.

Edit `pyproject.toml`'s `dependencies` list:

```toml
dependencies = [
    "aegra-api>=0.10.3",
    "aegra-cli>=0.10.3",
    "fastapi>=0.141.1",
    "google-api-python-client>=2.199.0",
    "google-auth-oauthlib>=1.4.0",
    "httpx>=0.28.1",
    "langchain>=1.3.16",
    "langchain-openai>=1.5.1",
    "langgraph>=1.2.11",
    "mcp>=2.0.0",
    "monarchmoney>=0.1.15",
    "pydantic-settings>=2.15.0",
    "pyjwt[crypto]>=2.13.0",
    "pyyaml>=6.0.3",
    "uvicorn[standard]>=0.52.3",
]
```

Remove `httpx` from `[dependency-groups].dev` — it is now a runtime
dependency of `eve.tools_client` (Task 2), not test-only.

Run `uv lock` and `uv sync` and confirm `uv run pytest` still collects with
no import errors before moving on.

- [ ] **Step 2: Write the failing settings test**

```python
def test_phase_3_settings_have_sane_defaults():
    s = Settings()
    assert s.tools_base_url == "http://eve-tools:8090"
    assert s.tools_api_key == ""
    assert s.skills_dir == Path("skills")
    assert s.specialist_max_iterations == 6
    assert s.dynamic_tools_cap == 8
```

Add `from pathlib import Path` to `tests/test_settings.py` if not already
imported.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_settings.py::test_phase_3_settings_have_sane_defaults -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'tools_base_url'`

- [ ] **Step 3: Add the fields**

In `src/eve/settings.py`, after the `embedding_dims` field and before the
memory fields:

```python
    # Phase 3 (Specialists + Skills). See docs/superpowers/specs/
    # 2026-08-21-eve-specialists-design.md section 7 and section 9.
    tools_base_url: str = "http://eve-tools:8090"
    tools_api_key: str = ""
    skills_dir: Path = Path("skills")
    # A specialist's own model+tool loop; not the outer eve<->tools cycle,
    # which relies on LangGraph's own recursion_limit (design doc section 3).
    specialist_max_iterations: int = 6
    dynamic_tools_cap: int = 8
```

These map to `EVE_TOOLS_BASE_URL`, `EVE_TOOLS_API_KEY`, `EVE_SKILLS_DIR`,
`EVE_SPECIALIST_MAX_ITERATIONS`, `EVE_DYNAMIC_TOOLS_CAP` under the existing
`env_prefix="EVE_"`. `EVE_TOOLS_API_KEY` is deliberately the same literal
env var name `eve-tools`' own settings (Task 14) will read — it is one shared
secret between the two processes, injected as the same Kubernetes Secret
value into both Deployments.

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_settings.py -v`
Expected: PASS, all tests including the new one.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/eve/settings.py tests/test_settings.py
git commit -m "feat: add Phase 3 dependencies and settings"
```

---

## Task 2: `eve-tools` HTTP client

**Files:**
- Create: `src/eve/tools_client.py`
- Test: `tests/test_tools_client.py`

**Interfaces:**
- Consumes: `Settings.tools_base_url`, `Settings.tools_api_key` (Task 1).
- Produces: `async def invoke(tool: str, arguments: dict, timeout: float = 15.0) -> str`
  — every specialist tool and the MCP-tool materializer (Tasks 5-7, 11) calls
  this and only this to reach `eve-tools`.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_tools_client.py"""
import json

import httpx
import pytest
import respx

from eve.tools_client import invoke


@pytest.fixture(autouse=True)
def _tools_settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", "http://eve-tools.test")
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")


@respx.mock
async def test_invoke_returns_the_result_as_json_text():
    respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, json={"result": {"state": "on"}})
    )
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert json.loads(result) == {"state": "on"}


@respx.mock
async def test_invoke_sends_the_shared_bearer_token():
    route = respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


@respx.mock
async def test_invoke_surfaces_a_server_side_error_as_a_string():
    respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, json={"error": "Home Assistant unreachable"})
    )
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert result == "error: Home Assistant unreachable"


@respx.mock
async def test_invoke_degrades_to_an_error_string_on_transport_failure():
    """A down eve-tools must not fail the whole turn - the caller is always a
    tool whose result goes straight to a model (design doc section 7)."""
    respx.post("http://eve-tools.test/invoke").mock(side_effect=httpx.ConnectError)
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert result.startswith("error:")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_tools_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.tools_client'`

- [ ] **Step 3: Implement**

```python
"""src/eve/tools_client.py

The one door from Eve's main container to eve-tools. Every call is an HTTP
request with a timeout; failures degrade to a returned error string rather
than a raised exception, because the caller is always a tool whose result
goes straight to a model - a raised exception here would fail the whole
turn instead of letting Eve explain the problem in her own words.
"""

from __future__ import annotations

import json
import logging

import httpx

from eve.settings import get_settings

logger = logging.getLogger(__name__)


async def invoke(tool: str, arguments: dict, timeout: float = 15.0) -> str:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.tools_base_url}/invoke",
                json={"tool": tool, "arguments": arguments},
                headers={"Authorization": f"Bearer {settings.tools_api_key}"},
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        logger.warning("eve-tools call to %r failed", tool, exc_info=True)
        return f"error: eve-tools unavailable ({exc.__class__.__name__})"

    if "error" in body:
        return f"error: {body['error']}"
    return json.dumps(body["result"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_tools_client.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/tools_client.py tests/test_tools_client.py
git commit -m "feat: eve-tools HTTP client"
```

---

## Task 3: Permission checks at the tool boundary

**Files:**
- Create: `src/eve/specialists/__init__.py` (empty)
- Create: `src/eve/specialists/permissions.py`
- Test: `tests/test_specialists_permissions.py`

**Interfaces:**
- Produces: `def permission_denial(permissions: list[str], required: str | list[str]) -> str | None`
  — `None` if held, else a string safe to return directly as a tool result.
  Consumed by Task 4 (coarse check) and Task 6 (fine check on `send_email`).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_specialists_permissions.py"""
from eve.specialists.permissions import permission_denial


def test_holding_the_single_required_permission_is_allowed():
    assert permission_denial(["home.control"], "home.control") is None


def test_missing_the_single_required_permission_is_denied():
    denial = permission_denial([], "home.control")
    assert denial is not None
    assert "home.control" in denial


def test_any_of_a_list_of_permissions_is_enough():
    assert permission_denial(["mail.read"], ["mail.read", "mail.send"]) is None


def test_holding_none_of_a_list_of_permissions_is_denied():
    denial = permission_denial([], ["mail.read", "mail.send"])
    assert denial is not None
    assert "mail.read" in denial and "mail.send" in denial
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_specialists_permissions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.specialists'`

- [ ] **Step 3: Implement**

```python
"""src/eve/specialists/permissions.py

Permission enforcement at the tool boundary. `family.yaml`'s own comment has
said "enforced at the tool boundary in Phase 3" since Phase 1; this is that
boundary, applied twice per design doc section 8 - coarse at the eve ->
specialist edge (Task 4), fine inside a specialist's own tools (Task 6's
send_email).
"""

from __future__ import annotations


def permission_denial(permissions: list[str], required: str | list[str]) -> str | None:
    """`None` if any of `required` is held; otherwise a string meant to be
    returned directly as a tool's result, so the turn continues and Eve
    explains the boundary instead of the graph erroring out."""
    needed = [required] if isinstance(required, str) else required
    if any(permission in permissions for permission in needed):
        return None
    return f"Permission denied: this action requires {' or '.join(needed)}."
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_specialists_permissions.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/specialists/__init__.py src/eve/specialists/permissions.py tests/test_specialists_permissions.py
git commit -m "feat: permission checks at the specialist tool boundary"
```

---

## Task 4: The specialist factory

This is the task that introduces tool-calling to a LangGraph agent for the
first time in this codebase, which needs a fake model capable of
`.bind_tools()` — `GenericFakeChatModel` (used everywhere in
`tests/test_graph.py` today) does not implement it and raises
`NotImplementedError`. Verified directly against this project's installed
`langchain-core==1.5.5` before writing this plan.

**Files:**
- Create: `src/eve/specialists/base.py`
- Modify: `tests/conftest.py` (add `FakeToolCallingModel`)
- Test: `tests/test_specialists_base.py`

**Interfaces:**
- Consumes: `eve.specialists.permissions.permission_denial` (Task 3),
  `eve.models.get_model`, `eve.models.Tier`, `eve.state.EveState`.
- Produces:
  `def build_specialist(name: str, tools: list[BaseTool], system_prompt: str, permission: str | list[str], model_factory=get_model) -> BaseTool`
  — returns a tool named `ask_{name}`, taking one `request: str` argument and
  returning a plain string. Consumed by Tasks 5, 6, 7, and 13.

- [ ] **Step 1: Add the shared fake model to `tests/conftest.py`**

```python
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel


class FakeToolCallingModel(GenericFakeChatModel):
    """`GenericFakeChatModel` raises `NotImplementedError` from `bind_tools` -
    fine before Phase 3, when nothing in the graph called it. Every model in
    `eve`'s loop and every specialist's loop binds tools unconditionally now
    (Task 13), so every graph-level test needs a fake that tolerates it."""

    def bind_tools(self, tools, **kwargs):
        return self
```

- [ ] **Step 2: Write the failing specialist-factory tests**

```python
"""tests/test_specialists_base.py"""
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from eve.specialists.base import build_specialist
from eve.state import EveState
from tests.conftest import FakeToolCallingModel

MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Vancouver",
    "permissions": ["home.control"],
    "local_time": "2026-08-21 09:00 PDT",
}
STATE: EveState = {
    "messages": [],
    "member": MEMBER,
    "system_prompt": "",
    "memory": None,
    "dynamic_tools": [],
}
CONFIG = {"configurable": {}}


@tool
async def get_widget(name: str) -> str:
    """Look up a widget."""
    return f"widget:{name}"


def _factory_with(*ai_messages):
    return lambda _tier: FakeToolCallingModel(messages=iter(ai_messages))


async def test_denies_the_call_before_touching_the_model():
    calls = []

    def factory(_tier):
        calls.append(1)
        return FakeToolCallingModel(messages=iter([AIMessage("should not run")]))

    ask = build_specialist(
        name="widgets",
        tools=[get_widget],
        system_prompt="You manage widgets.",
        permission="widgets.manage",
        model_factory=factory,
    )
    result = await ask.ainvoke(
        {"request": "get the sprocket", "state": STATE, "config": CONFIG}
    )
    assert "Permission denied" in result
    assert "widgets.manage" in result
    assert calls == [], "the model must never be called on a denied request"


async def test_runs_the_inner_tool_loop_and_returns_the_final_answer():
    tool_call = {
        "name": "get_widget",
        "args": {"name": "sprocket"},
        "id": "call-1",
        "type": "tool_call",
    }
    ask = build_specialist(
        name="home",
        tools=[get_widget],
        system_prompt="You manage widgets.",
        permission="home.control",
        model_factory=_factory_with(
            AIMessage(content="", tool_calls=[tool_call]),
            AIMessage(content="Found it: widget:sprocket"),
        ),
    )
    result = await ask.ainvoke(
        {"request": "look up the sprocket", "state": STATE, "config": CONFIG}
    )
    assert result == "Found it: widget:sprocket"


async def test_allows_any_of_a_permission_list():
    ask = build_specialist(
        name="mail",
        tools=[],
        system_prompt="You manage mail.",
        permission=["mail.read", "mail.send"],
        model_factory=_factory_with(AIMessage("ok")),
    )
    member_with_read_only = {**MEMBER, "permissions": ["mail.read"]}
    state = {**STATE, "member": member_with_read_only}
    result = await ask.ainvoke({"request": "summarise my inbox", "state": state, "config": CONFIG})
    assert result == "ok"
```

`eve.state.EveState` does not yet have `dynamic_tools` or accept `memory:
None` at this point — Task 8 adds `dynamic_tools`; `memory` is already
optional via `.get()` everywhere it is read, so `None` is a valid value here.

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_specialists_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.specialists.base'`

- [ ] **Step 4: Implement**

```python
"""src/eve/specialists/base.py

One factory for every specialist: a small tool-calling loop on
Tier.MECHANICAL (langchain.agents.create_agent - NOT the deprecated
langgraph.prebuilt.create_react_agent, verified removed in LangGraph V2),
wrapped as a single opaque tool for eve's own loop. ADR 0001: specialists
keep their own agentic loop rather than becoming a flat tool list on Eve -
this factory is the one place that loop is built, so Home, Mail, and
Finances (Tasks 5-7) share it instead of three hand-rolled graphs.
"""

from __future__ import annotations

from time import perf_counter
from typing import Annotated

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from opentelemetry import trace

from eve.models import Tier, get_model
from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.state import EveState


def build_specialist(
    name: str,
    tools: list[BaseTool],
    system_prompt: str,
    permission: str | list[str],
    model_factory=get_model,
) -> BaseTool:
    agent = create_agent(
        model_factory(Tier.MECHANICAL), tools, system_prompt=system_prompt
    )

    async def ask(
        request: str,
        state: Annotated[EveState, InjectedState],
        config: RunnableConfig,
    ) -> str:
        # Design doc section 10: "which specialists actually get used" and
        # "is the permission boundary being hit in practice" are both
        # questions that need a number, not an assumption - same discipline
        # as memory/recall.py's eve.recall.* attributes.
        span = trace.get_current_span()
        span.set_attribute("eve.specialist.called", name)
        member = state["member"]
        denial = permission_denial(member["permissions"], permission)
        if denial:
            span.set_attribute("eve.specialist.permission_denied", True)
            return denial
        started = perf_counter()
        inner_config: RunnableConfig = {
            **config,
            "configurable": {**config.get("configurable", {}), "member": member},
            "recursion_limit": get_settings().specialist_max_iterations,
        }
        result = await agent.ainvoke(
            {"messages": [HumanMessage(request)]}, inner_config
        )
        span.set_attribute(
            "eve.specialist.latency_ms", round((perf_counter() - started) * 1000, 1)
        )
        return str(result["messages"][-1].content)

    ask.__name__ = f"ask_{name}"
    ask.__doc__ = f"Ask the {name} specialist to handle a request in its domain."
    return tool(ask)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_specialists_base.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add src/eve/specialists/base.py tests/conftest.py tests/test_specialists_base.py
git commit -m "feat: the specialist factory (shared tool-calling loop)"
```

---

## Task 5: The Home specialist

**Files:**
- Create: `src/eve/specialists/home.py`
- Test: `tests/test_specialists_home.py`

**Interfaces:**
- Consumes: `build_specialist` (Task 4), `eve.tools_client.invoke` (Task 2).
- Produces: `ask_home: BaseTool`, permission `"home.control"`. Consumed by
  Task 13.

`build_specialist` (Task 4) captures `model_factory(Tier.MECHANICAL)` once,
at the module-level call that builds `ask_home` — so a unit test must
substitute the model *before* `eve.specialists.home` is (re-)imported, via
`importlib.reload`, mirroring the pattern Task 6 and Task 7 also use.

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_specialists_home.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.home as home_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


async def test_ask_home_calls_get_state_through_eve_tools(monkeypatch):
    tool_call = {
        "name": "get_state",
        "args": {"entity_id": "light.kitchen"},
        "id": "call-1",
        "type": "tool_call",
    }
    monkeypatch.setattr(
        "eve.specialists.home._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="The kitchen light is on."),
                ]
            )
        ),
    )
    importlib.reload(home_module)
    mock_invoke = AsyncMock(return_value='{"state": "on"}')
    monkeypatch.setattr(home_module, "invoke", mock_invoke)

    result = await home_module.ask_home.ainvoke(
        {"request": "is the kitchen light on?", "state": STATE, "config": CONFIG}
    )
    assert result == "The kitchen light is on."
    mock_invoke.assert_awaited_once_with(
        "home.get_state", {"entity_id": "light.kitchen"}
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_specialists_home.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.specialists.home'`

- [ ] **Step 3: Implement**

```python
"""src/eve/specialists/home.py

Home specialist: Home Assistant device control via eve-tools. The specialist
never talks to Home Assistant directly - every tool call here is a thin HTTP
relay (design doc section 4, section 7).
"""

from __future__ import annotations

from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You control the family's smart home via Home Assistant. Look up a "
    "device's state before changing it if the request is ambiguous. Report "
    "exactly what you changed, in one sentence."
)


def _model_for_test():
    """Indirection so unit tests can substitute a fake model, via
    importlib.reload, without a live LiteLLM call at import time."""
    return get_model(Tier.MECHANICAL)


@tool
async def get_state(entity_id: str) -> str:
    """Read the current state of a Home Assistant entity, e.g. 'light.kitchen'."""
    return await invoke("home.get_state", {"entity_id": entity_id})


@tool
async def call_service(
    domain: str, service: str, entity_id: str, data: dict | None = None
) -> str:
    """Call a Home Assistant service, e.g. domain='light', service='turn_on'."""
    return await invoke(
        "home.call_service",
        {
            "domain": domain,
            "service": service,
            "entity_id": entity_id,
            "data": data or {},
        },
    )


ask_home = build_specialist(
    name="home",
    tools=[get_state, call_service],
    system_prompt=SYSTEM_PROMPT,
    permission="home.control",
    model_factory=lambda _tier: _model_for_test(),
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_specialists_home.py -v`
Expected: PASS, 1 test.

- [ ] **Step 5: Commit**

```bash
git add src/eve/specialists/home.py tests/test_specialists_home.py
git commit -m "feat: the Home specialist"
```

---

## Task 6: The Mail specialist

Follows Task 5's exact shape, with one addition: `send_email` carries its own
fine-grained permission check (`mail.send`), separate from the coarse
`ask_mail` check (`mail.read` OR `mail.send`) — a member with only
`mail.read` can ask Eve to summarize their inbox but not send on their
behalf (design doc section 4).

**Files:**
- Create: `src/eve/specialists/mail.py`
- Test: `tests/test_specialists_mail.py`

**Interfaces:**
- Consumes: `build_specialist` (Task 4), `permission_denial` (Task 3),
  `invoke` (Task 2).
- Produces: `ask_mail: BaseTool`, permission `["mail.read", "mail.send"]`.
  Consumed by Task 13.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_specialists_mail.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.mail as mail_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


def _reload_with_model(monkeypatch, *ai_messages):
    monkeypatch.setattr(
        "eve.specialists.mail._model_for_test",
        lambda: FakeToolCallingModel(messages=iter(ai_messages)),
    )
    importlib.reload(mail_module)
    return mail_module


async def test_send_email_is_denied_without_mail_send(monkeypatch):
    tool_call = {
        "name": "send_email",
        "args": {"to": "a@b.com", "subject": "hi", "body": "hi"},
        "id": "call-1",
        "type": "tool_call",
    }
    module = _reload_with_model(
        monkeypatch,
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="done"),
    )
    monkeypatch.setattr(module, "invoke", AsyncMock(return_value="sent"))
    read_only_member = {**MEMBER, "permissions": ["mail.read"]}
    state = {**STATE, "member": read_only_member}
    result = await module.ask_mail.ainvoke(
        {"request": "email a@b.com saying hi", "state": state, "config": CONFIG}
    )
    assert "Permission denied" in result
    module.invoke.assert_not_awaited()


async def test_send_email_succeeds_with_mail_send(monkeypatch):
    tool_call = {
        "name": "send_email",
        "args": {"to": "a@b.com", "subject": "hi", "body": "hi there"},
        "id": "call-1",
        "type": "tool_call",
    }
    module = _reload_with_model(
        monkeypatch,
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Sent."),
    )
    mock_invoke = AsyncMock(return_value='{"sent": true}')
    monkeypatch.setattr(module, "invoke", mock_invoke)
    sender_member = {**MEMBER, "permissions": ["mail.send"]}
    state = {**STATE, "member": sender_member}
    result = await module.ask_mail.ainvoke(
        {"request": "email a@b.com saying hi", "state": state, "config": CONFIG}
    )
    assert result == "Sent."
    mock_invoke.assert_awaited_once_with(
        "mail.send_email",
        {"member_sub": "sub-noah", "to": "a@b.com", "subject": "hi", "body": "hi there"},
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_specialists_mail.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.specialists.mail'`

- [ ] **Step 3: Implement**

```python
"""src/eve/specialists/mail.py

Mail specialist: Gmail via eve-tools. `send_email` is gated separately from
the read tools (design doc section 4) - the coarse ask_mail check requires
mail.read OR mail.send, but sending additionally requires mail.send.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.specialists.permissions import permission_denial
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You manage the requesting family member's Gmail. Summarise before "
    "quoting a whole thread. Never send an email without the exact "
    "recipient, subject, and body the request specified or clearly implied."
)


def _model_for_test():
    return get_model(Tier.MECHANICAL)


@tool
async def list_messages(query: str, config: RunnableConfig) -> str:
    """Search Gmail. Gmail query syntax, e.g. 'is:unread from:school'."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke("mail.list_messages", {"member_sub": member_sub, "query": query})


@tool
async def get_thread(thread_id: str, config: RunnableConfig) -> str:
    """Read a full Gmail thread by id."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke(
        "mail.get_thread", {"member_sub": member_sub, "thread_id": thread_id}
    )


@tool
async def send_email(to: str, subject: str, body: str, config: RunnableConfig) -> str:
    """Send an email. Requires the mail.send permission."""
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), "mail.send")
    if denial:
        return denial
    return await invoke(
        "mail.send_email",
        {"member_sub": member["sub"], "to": to, "subject": subject, "body": body},
    )


ask_mail = build_specialist(
    name="mail",
    tools=[list_messages, get_thread, send_email],
    system_prompt=SYSTEM_PROMPT,
    permission=["mail.read", "mail.send"],
    model_factory=lambda _tier: _model_for_test(),
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_specialists_mail.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/specialists/mail.py tests/test_specialists_mail.py
git commit -m "feat: the Mail specialist"
```

---

## Task 7: The Finances specialist

**Files:**
- Create: `src/eve/specialists/finances.py`
- Test: `tests/test_specialists_finances.py`

**Interfaces:**
- Consumes: `build_specialist` (Task 4), `invoke` (Task 2).
- Produces: `ask_finances: BaseTool`, permission `"finances"`. Consumed by
  Task 13.

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_specialists_finances.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.finances as finances_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


async def test_ask_finances_reads_transactions_through_eve_tools(monkeypatch):
    tool_call = {
        "name": "list_transactions",
        "args": {"limit": 5, "category": None},
        "id": "call-1",
        "type": "tool_call",
    }
    monkeypatch.setattr(
        "eve.specialists.finances._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="You spent $42 at the grocery store."),
                ]
            )
        ),
    )
    importlib.reload(finances_module)
    mock_invoke = AsyncMock(return_value='{"transactions": []}')
    monkeypatch.setattr(finances_module, "invoke", mock_invoke)
    member = {**MEMBER, "permissions": ["finances"]}
    state = {**STATE, "member": member}
    result = await finances_module.ask_finances.ainvoke(
        {"request": "what did I spend recently?", "state": state, "config": CONFIG}
    )
    assert result == "You spent $42 at the grocery store."
    mock_invoke.assert_awaited_once_with(
        "finances.list_transactions", {"limit": 5, "category": None}
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_specialists_finances.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.specialists.finances'`

- [ ] **Step 3: Implement**

```python
"""src/eve/specialists/finances.py

Finances specialist: Monarch Money via eve-tools. Read-only - Monarch's
write surface (categorising, flagging) is deferred to when a concrete need
for it shows up (design doc section 4).
"""

from __future__ import annotations

from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You answer questions about the family's finances using Monarch Money "
    "data. State dollar amounts exactly as returned; never estimate."
)


def _model_for_test():
    return get_model(Tier.MECHANICAL)


@tool
async def list_transactions(limit: int = 20, category: str | None = None) -> str:
    """List recent transactions, optionally filtered by category."""
    return await invoke(
        "finances.list_transactions", {"limit": limit, "category": category}
    )


@tool
async def get_budgets() -> str:
    """Read current budget and cash-flow summary."""
    return await invoke("finances.get_budgets", {})


ask_finances = build_specialist(
    name="finances",
    tools=[list_transactions, get_budgets],
    system_prompt=SYSTEM_PROMPT,
    permission="finances",
    model_factory=lambda _tier: _model_for_test(),
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_specialists_finances.py -v`
Expected: PASS, 1 test.

- [ ] **Step 5: Commit**

```bash
git add src/eve/specialists/finances.py tests/test_specialists_finances.py
git commit -m "feat: the Finances specialist"
```

---

## Task 8: State and skill shapes

**Files:**
- Modify: `src/eve/state.py`
- Create: `src/eve/skills/__init__.py` (empty)
- Create: `src/eve/skills/types.py`
- Test: `tests/test_state.py` (new file)

**Interfaces:**
- Produces: `EveState.dynamic_tools: list[DynamicToolSpec]`,
  `eve.skills.types.DynamicToolSpec`, `eve.skills.types.SkillMatch`. Consumed
  by Tasks 9-13.

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_state.py"""
from eve.skills.types import DynamicToolSpec
from eve.state import EveState


def test_eve_state_carries_dynamic_tools():
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "do_thing",
        "description": "Does a thing.",
        "schema": {"properties": {}},
    }
    state: EveState = {
        "messages": [],
        "member": {
            "sub": "sub-noah",
            "name": "Noah",
            "role": "adult",
            "timezone": "America/Vancouver",
            "permissions": [],
            "local_time": "2026-08-21 09:00 PDT",
        },
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [spec],
    }
    assert state["dynamic_tools"][0]["tool_name"] == "do_thing"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.skills'`

- [ ] **Step 3: Implement**

```python
"""src/eve/skills/types.py

Shapes only. `DynamicToolSpec` is a materializable REFERENCE to an MCP
tool, never a live callable - see design doc section 5.1: Aegra
checkpoints EveState to Postgres across every turn in a thread, and a
value closing over a live connection would either fail to serialize or
silently break on the next turn's rehydration.
"""

from __future__ import annotations

from typing import TypedDict


class DynamicToolSpec(TypedDict):
    server_id: str
    tool_name: str
    description: str
    schema: dict  # JSON schema for the tool's arguments


class SkillMatch(TypedDict):
    kind: str  # "procedure" | "mcp_tool"
    name: str
    content: str  # procedure text, or a description for an mcp_tool match
    spec: DynamicToolSpec | None
```

In `src/eve/state.py`, add the import and field:

```python
from eve.memory.types import MemoryBundle
from eve.skills.types import DynamicToolSpec


class EveState(TypedDict):
    messages: Annotated[list, add_messages]
    member: MemberContext
    system_prompt: str
    memory: MemoryBundle
    # Specs only - see eve.skills.types.DynamicToolSpec. Materialized into
    # real callables fresh on every model call (eve.skills.materialize,
    # Task 11), never stored as one.
    dynamic_tools: list[DynamicToolSpec]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing unit suite to confirm nothing else broke**

Run: `uv run pytest -v`
Expected: PASS. `EveState` gaining a required `TypedDict` key is a
type-checker-level change only — nothing constructs `EveState` positionally,
and every existing call site already builds it as a dict literal or via
`.get()`, so no runtime break is expected. If any test does fail here because
it builds an `EveState` literal without `dynamic_tools`, add the field there
too before proceeding — do not skip this check.

- [ ] **Step 6: Commit**

```bash
git add src/eve/state.py src/eve/skills/__init__.py src/eve/skills/types.py tests/test_state.py
git commit -m "feat: EveState.dynamic_tools and skill shapes"
```

---

## Task 9: The skills registry

**Files:**
- Create: `src/eve/skills/registry.py`
- Create: `src/eve/skills/mcp_registry.py`
- Test: `tests/test_skills_registry.py`

**Interfaces:**
- Consumes: `Settings.skills_dir` (Task 1), `DynamicToolSpec` (Task 8).
- Produces: `class Skill` (frozen dataclass: `name, description, kind,
  content, spec`), `def load_skills(mcp_tools: list[DynamicToolSpec] | None = None) -> list[Skill]`,
  `def register(spec: DynamicToolSpec) -> None`,
  `def registered_mcp_tools() -> list[DynamicToolSpec]`. Consumed by Task 10.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_skills_registry.py"""
from pathlib import Path

from eve.skills import mcp_registry
from eve.skills.registry import load_skills
from eve.skills.types import DynamicToolSpec


def test_loads_a_skill_md_with_frontmatter(tmp_path, monkeypatch):
    skill_dir = tmp_path / "greet-warmly"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: greet-warmly\n"
        "description: How to greet a family member warmly.\n"
        "---\n"
        "Use their first name and ask about their day.\n"
    )
    monkeypatch.setattr(
        "eve.settings.get_settings",
        lambda: type("S", (), {"skills_dir": tmp_path})(),
    )
    import eve.skills.registry as registry_module

    monkeypatch.setattr(registry_module, "get_settings", lambda: type(
        "S", (), {"skills_dir": tmp_path}
    )())
    skills = load_skills()
    assert len(skills) == 1
    assert skills[0].name == "greet-warmly"
    assert skills[0].kind == "procedure"
    assert "first name" in skills[0].content


def test_missing_skills_dir_yields_no_procedures(tmp_path, monkeypatch):
    import eve.skills.registry as registry_module

    monkeypatch.setattr(
        registry_module, "get_settings", lambda: type("S", (), {"skills_dir": tmp_path / "nope"})()
    )
    assert load_skills() == []


def test_registered_mcp_tools_become_skills(tmp_path, monkeypatch):
    import eve.skills.registry as registry_module

    monkeypatch.setattr(
        registry_module, "get_settings", lambda: type("S", (), {"skills_dir": tmp_path})()
    )
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "Roll a die and return the result.",
        "schema": {"properties": {}},
    }
    skills = load_skills(mcp_tools=[spec])
    assert len(skills) == 1
    assert skills[0].kind == "mcp_tool"
    assert skills[0].spec == spec


def test_mcp_registry_round_trips():
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "Roll a die.",
        "schema": {"properties": {}},
    }
    mcp_registry.register(spec)
    assert spec in mcp_registry.registered_mcp_tools()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_skills_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.skills.registry'`

- [ ] **Step 3: Implement**

```python
"""src/eve/skills/registry.py

Loads the skills index: SKILL.md procedures from disk, and MCP tool
descriptions handed in by the caller (eve.skills.mcp_registry). Rebuilt on
every call rather than cached - the corpus is a handful of files and
process-lifetime metadata, and correctness (a newly-added SKILL.md showing
up without a restart) is worth more than the cache here.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from eve.settings import get_settings
from eve.skills.types import DynamicToolSpec


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    kind: str  # "procedure" | "mcp_tool"
    content: str
    spec: DynamicToolSpec | None = None


def _load_skill_md(path) -> Skill:
    text = path.read_text()
    if text.startswith("---"):
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
    else:
        meta, body = {}, text
    return Skill(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        kind="procedure",
        content=body.strip(),
    )


def load_skills(mcp_tools: list[DynamicToolSpec] | None = None) -> list[Skill]:
    skills_dir = get_settings().skills_dir
    procedures = (
        [_load_skill_md(p) for p in sorted(skills_dir.glob("*/SKILL.md"))]
        if skills_dir.exists()
        else []
    )
    mcp_skills = [
        Skill(
            name=f"{spec['server_id']}.{spec['tool_name']}",
            description=spec["description"],
            kind="mcp_tool",
            content=spec["description"],
            spec=spec,
        )
        for spec in (mcp_tools or [])
    ]
    return [*procedures, *mcp_skills]
```

```python
"""src/eve/skills/mcp_registry.py

Static metadata for registered MCP servers - name, description, and
argument schema, known without opening a connection. Opening the connection
and calling the tool is eve-tools' job (eve_tools.mcp_dispatch, Task 15);
this side only needs enough to rank and describe a tool in search_skills
(Task 10). Empty in production until a concrete skill needs one (design
doc section 2.1's non-goal); populated in tests with a mock server's specs.
"""

from __future__ import annotations

from eve.skills.types import DynamicToolSpec

_REGISTERED: list[DynamicToolSpec] = []


def register(spec: DynamicToolSpec) -> None:
    _REGISTERED.append(spec)


def registered_mcp_tools() -> list[DynamicToolSpec]:
    return list(_REGISTERED)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_skills_registry.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/skills/registry.py src/eve/skills/mcp_registry.py tests/test_skills_registry.py
git commit -m "feat: the skills registry"
```

---

## Task 10: `search_skills`

**Files:**
- Create: `src/eve/skills/search.py`
- Test: `tests/test_skills_search.py`

**Interfaces:**
- Consumes: `load_skills`, `mcp_registry.registered_mcp_tools` (Task 9),
  `eve.memory.embed.embed_query`, `EveState` (Task 8),
  `Settings.dynamic_tools_cap` (Task 1).
- Produces: `search_skills: BaseTool` (a `Command`-returning tool updating
  `messages` and `dynamic_tools`), `async def rank_skills(query, skills, top_k=3) -> list[Skill]`.
  Consumed by Task 13.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_skills_search.py"""
from langchain_core.messages import ToolMessage

from eve.skills.registry import Skill
from eve.skills.search import rank_skills, search_skills
from eve.skills.types import DynamicToolSpec
from tests.test_specialists_base import MEMBER


async def _fake_embed(text: str) -> list[float]:
    # A tiny deterministic embedding: closer strings get closer vectors by
    # counting shared words, so ranking is exercisable without a real model.
    words = set(text.lower().split())
    return [1.0 if w in words else 0.0 for w in ("greet", "warmly", "dice", "roll")]


async def test_rank_skills_prefers_the_closer_match(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    skills = [
        Skill(name="greet-warmly", description="greet warmly", kind="procedure", content="..."),
        Skill(name="roll-dice", description="roll dice", kind="procedure", content="..."),
    ]
    ranked = await rank_skills("please greet warmly", skills, top_k=1)
    assert ranked[0].name == "greet-warmly"


async def test_search_skills_returns_a_procedure_directly(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    monkeypatch.setattr(
        "eve.skills.search.load_skills",
        lambda mcp_tools=None: [
            Skill(
                name="greet-warmly",
                description="greet warmly",
                kind="procedure",
                content="Use their first name.",
            )
        ],
    )
    state = {"dynamic_tools": []}
    command = await search_skills.ainvoke(
        {
            "query": "how should I greet warmly",
            "state": state,
            "tool_call_id": "call-1",
        }
    )
    message = command.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert "Use their first name." in message.content
    assert command.update.get("dynamic_tools", []) == []


async def test_search_skills_adds_an_mcp_match_to_dynamic_tools(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "roll dice",
        "schema": {"properties": {}},
    }
    monkeypatch.setattr(
        "eve.skills.search.load_skills",
        lambda mcp_tools=None: [
            Skill(
                name="mock-server.roll_dice",
                description="roll dice",
                kind="mcp_tool",
                content="roll dice",
                spec=spec,
            )
        ],
    )
    state = {"dynamic_tools": []}
    command = await search_skills.ainvoke(
        {"query": "roll dice for me", "state": state, "tool_call_id": "call-1"}
    )
    assert command.update["dynamic_tools"] == [spec]


async def test_search_skills_caps_dynamic_tools(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    monkeypatch.setattr("eve.skills.search.get_settings", lambda: type(
        "S", (), {"dynamic_tools_cap": 1}
    )())
    existing: DynamicToolSpec = {
        "server_id": "a", "tool_name": "x", "description": "roll dice", "schema": {}
    }
    new_spec: DynamicToolSpec = {
        "server_id": "b", "tool_name": "y", "description": "roll dice", "schema": {}
    }
    monkeypatch.setattr(
        "eve.skills.search.load_skills",
        lambda mcp_tools=None: [
            Skill(name="b.y", description="roll dice", kind="mcp_tool", content="", spec=new_spec)
        ],
    )
    state = {"dynamic_tools": [existing]}
    command = await search_skills.ainvoke(
        {"query": "roll dice", "state": state, "tool_call_id": "call-1"}
    )
    assert command.update["dynamic_tools"] == [new_spec]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_skills_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.skills.search'`

- [ ] **Step 3: Implement**

```python
"""src/eve/skills/search.py

search_skills: the one tool that turns Eve's fixed toolset into an
extensible one. A SKILL.md match returns a procedure directly as the tool's
result - knowledge, not a new capability, so nothing about the bound-tool
list changes. An MCP match is different: it appends a DynamicToolSpec to
state via a Command update, materialized into a real callable on the next
model call (eve.skills.materialize, Task 11; wired into the graph in Task
13) - never a live callable itself, because Aegra checkpoints EveState to
Postgres across every turn (design doc section 5.1).
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from opentelemetry import trace

from eve.memory.embed import embed_query
from eve.settings import get_settings
from eve.skills.mcp_registry import registered_mcp_tools
from eve.skills.registry import Skill, load_skills
from eve.state import EveState


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def rank_skills(query: str, skills: list[Skill], top_k: int = 3) -> list[Skill]:
    if not skills:
        return []
    query_vec = await embed_query(query)
    scored = [
        (_dot(query_vec, await embed_query(skill.description or skill.name)), skill)
        for skill in skills
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [skill for _, skill in scored[:top_k]]


@tool
async def search_skills(
    query: str,
    state: Annotated[EveState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Search for a known procedure or a newly-available tool matching a
    request outside your normal toolset."""
    # Design doc section 10: "is search_skills ever called, or is the whole
    # mechanism unused" and "how many dynamically-bound tools accumulate"
    # are both questions this attribute pair exists to answer with a number.
    trace.get_current_span().set_attribute("eve.skills.search_used", True)
    skills = load_skills(mcp_tools=registered_mcp_tools())
    matches = await rank_skills(query, skills)
    if not matches:
        return Command(
            update={
                "messages": [
                    ToolMessage("No matching skill or tool found.", tool_call_id=tool_call_id)
                ]
            }
        )

    procedures = [m for m in matches if m.kind == "procedure"]
    mcp_matches = [m for m in matches if m.kind == "mcp_tool" and m.spec]

    existing = state.get("dynamic_tools", [])
    new_specs = [m.spec for m in mcp_matches if m.spec not in existing]
    cap = get_settings().dynamic_tools_cap
    merged = (existing + new_specs)[-cap:]
    trace.get_current_span().set_attribute("eve.skills.mcp_bound", len(merged))

    lines = [f"# {m.name}\n{m.content}" for m in procedures]
    lines += [f"Tool available: {m.name} - {m.description}" for m in mcp_matches]
    content = "\n\n".join(lines)

    return Command(
        update={
            "messages": [ToolMessage(content, tool_call_id=tool_call_id)],
            "dynamic_tools": merged,
        }
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_skills_search.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/skills/search.py tests/test_skills_search.py
git commit -m "feat: search_skills with dynamic MCP tool binding"
```

---

## Task 11: Materializing a `DynamicToolSpec`

**Files:**
- Create: `src/eve/skills/materialize.py`
- Test: `tests/test_skills_materialize.py`

**Interfaces:**
- Consumes: `DynamicToolSpec` (Task 8), `invoke` (Task 2).
- Produces: `def materialize(spec: DynamicToolSpec) -> StructuredTool`.
  Consumed by Task 13.

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_skills_materialize.py"""
from unittest.mock import AsyncMock

from eve.skills.materialize import materialize
from eve.skills.types import DynamicToolSpec


async def test_materialized_tool_calls_the_mcp_dispatcher(monkeypatch):
    mock_invoke = AsyncMock(return_value='{"total": 4}')
    monkeypatch.setattr("eve.skills.materialize.invoke", mock_invoke)
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "Roll a die with the given number of sides.",
        "schema": {"properties": {"sides": {"type": "integer"}}},
    }
    tool_obj = materialize(spec)
    assert tool_obj.name == "mock-server_roll_dice"
    result = await tool_obj.ainvoke({"sides": 6})
    assert result == '{"total": 4}'
    mock_invoke.assert_awaited_once_with(
        "mcp.invoke",
        {"server_id": "mock-server", "tool_name": "roll_dice", "arguments": {"sides": 6}},
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_skills_materialize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.skills.materialize'`

- [ ] **Step 3: Implement**

```python
"""src/eve/skills/materialize.py

Turns a DynamicToolSpec back into a callable tool, freshly, on every model
call - design doc section 5.1 explains why state can only ever hold the
spec. Only string/integer/number/boolean argument types are supported;
a schema with a richer type (array, object, enum) falls back to str, which
is wrong for validation but not for dispatch - eve-tools receives whatever
JSON-compatible value the model supplies either way.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import create_model

from eve.skills.types import DynamicToolSpec
from eve.tools_client import invoke

_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _args_model(tool_name: str, schema: dict):
    fields = {
        prop: (_TYPE_MAP.get(info.get("type", "string"), str), ...)
        for prop, info in schema.get("properties", {}).items()
    }
    return create_model(f"{tool_name}Args", **fields)


def materialize(spec: DynamicToolSpec) -> StructuredTool:
    args_model = _args_model(spec["tool_name"], spec["schema"])

    async def _call(**kwargs) -> str:
        return await invoke(
            "mcp.invoke",
            {
                "server_id": spec["server_id"],
                "tool_name": spec["tool_name"],
                "arguments": kwargs,
            },
        )

    return StructuredTool.from_function(
        coroutine=_call,
        name=f"{spec['server_id']}_{spec['tool_name']}",
        description=spec["description"],
        args_schema=args_model,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_skills_materialize.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eve/skills/materialize.py tests/test_skills_materialize.py
git commit -m "feat: materialize DynamicToolSpec into a callable tool"
```

---

## Task 12: `search_memory`

**Files:**
- Create: `src/eve/memory/search.py`
- Test: `tests/test_memory_search.py`

**Interfaces:**
- Consumes: `eve.memory.store.search_episodic_lexical`,
  `eve.memory.store.search_episodic_vector`, `eve.memory.embed.embed_query`,
  `eve.memory.ranking.fuse`, `EveState` (Task 8).
- Produces: `search_memory: BaseTool`. Consumed by Task 13.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_memory_search.py"""
from datetime import UTC, datetime

from eve.memory.search import search_memory
from eve.memory.types import Memory

NOW = datetime.now(UTC)


def _memory(id_, content):
    return Memory(
        id=id_, layer="episodic", scope_kind="member", scope_id="sub-noah",
        kind="event", subject=None, content=content, confidence=0.7,
        salience=0.5, created_at=NOW, last_seen_at=NOW,
    )


async def test_search_memory_merges_lexical_and_vector_results(monkeypatch):
    monkeypatch.setattr(
        "eve.memory.search.search_episodic_lexical",
        lambda sub, query, limit=10: [_memory("1", "Decided to replace the dishwasher.")],
    )
    monkeypatch.setattr(
        "eve.memory.search.search_episodic_vector",
        lambda sub, embedding, limit=10: [_memory("2", "The kitchen needs a new dishwasher.")],
    )
    monkeypatch.setattr("eve.memory.search.embed_query", lambda q: [0.1, 0.2])
    state = {"member": {"sub": "sub-noah"}}
    result = await search_memory.ainvoke({"query": "dishwasher", "state": state})
    assert "Decided to replace the dishwasher." in result
    assert "The kitchen needs a new dishwasher." in result


async def test_search_memory_degrades_to_lexical_only_on_embedding_failure(monkeypatch):
    monkeypatch.setattr(
        "eve.memory.search.search_episodic_lexical",
        lambda sub, query, limit=10: [_memory("1", "Cooper's vet appointment is Tuesday.")],
    )

    async def _fail(_query):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr("eve.memory.search.embed_query", _fail)
    state = {"member": {"sub": "sub-noah"}}
    result = await search_memory.ainvoke({"query": "vet", "state": state})
    assert "Cooper's vet appointment is Tuesday." in result


async def test_search_memory_reports_nothing_found():
    state = {"member": {"sub": "sub-noah"}}
    result = await search_memory.ainvoke({"query": "nonexistent", "state": state})
    assert result == "Nothing found."
```

The third test relies on the real `search_episodic_lexical`/
`search_episodic_vector`, which need a database connection — mark it
`@pytest.mark.integration` instead once `docker-compose.test.yml` is
running, or monkeypatch both to return `[]` to keep it a unit test:

```python
async def test_search_memory_reports_nothing_found(monkeypatch):
    monkeypatch.setattr("eve.memory.search.search_episodic_lexical", lambda *a, **k: [])
    monkeypatch.setattr("eve.memory.search.search_episodic_vector", lambda *a, **k: [])
    monkeypatch.setattr("eve.memory.search.embed_query", lambda q: [0.0])
    state = {"member": {"sub": "sub-noah"}}
    result = await search_memory.ainvoke({"query": "nonexistent", "state": state})
    assert result == "Nothing found."
```

Use this corrected version.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_memory_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.memory.search'`

- [ ] **Step 3: Implement**

```python
"""src/eve/memory/search.py

search_memory: deliberate recall, available as a tool. Unlike the
unconditional `recall` node (memory/recall.py), this has no time budget -
it only ever runs mid-turn, after the first token has already streamed
(design doc section 6, honoring memory design section 13's own commitment).
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.memory.embed import embed_query
from eve.memory.ranking import fuse
from eve.memory.store import search_episodic_lexical, search_episodic_vector
from eve.memory.types import Memory
from eve.state import EveState


@tool
async def search_memory(query: str, state: Annotated[EveState, InjectedState]) -> str:
    """Search past conversations and household memory for something not in
    your current context - a decision, an event, a detail from weeks ago."""
    sub = state["member"]["sub"]
    lexical = await search_episodic_lexical(sub, query, limit=10)
    try:
        vector = await search_episodic_vector(sub, await embed_query(query), limit=10)
    except Exception:
        vector = []

    by_id = {m.id: m for m in (*lexical, *vector)}
    order = fuse([m.id for m in lexical], [m.id for m in vector])
    results: list[Memory] = [by_id[i] for i in order][:10]
    if not results:
        return "Nothing found."
    return "\n".join(f"- {m.content}" for m in results)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_memory_search.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/memory/search.py tests/test_memory_search.py
git commit -m "feat: search_memory, deliberate recall as a tool"
```

---

## Task 13: Wire the tools loop into the graph

**Files:**
- Modify: `src/eve/graph.py`
- Modify: `tests/test_graph.py`

**Interfaces:**
- Consumes: `ask_home`, `ask_mail`, `ask_finances` (Tasks 5-7),
  `search_skills` (Task 10), `search_memory` (Task 12), `materialize`
  (Task 11), `FakeToolCallingModel` (Task 4).
- Produces: the compiled graph now has an `eve <-> tools` cycle. No new
  public interface for later tasks — this is where everything so far gets
  used together for the first time.

- [ ] **Step 1: Update every existing fake model in `tests/test_graph.py`**

`eve`'s node calls `.bind_tools(...)` unconditionally once this task lands.
Every fake model in this file needs to tolerate that. Replace
`GenericFakeChatModel` with `FakeToolCallingModel` (from `tests/conftest.py`,
Task 4) everywhere it appears — as `_fake_factory`'s return value, and as the
base class of `RecordingModel` in three separate tests.

```python
from tests.conftest import FakeToolCallingModel


def _fake_factory(_tier):
    return FakeToolCallingModel(messages=iter([AIMessage(content="Hi Noah.")]))


class RecordingModel(FakeToolCallingModel):
    async def ainvoke(self, input, config=None, **kwargs):
        seen["messages"] = input
        return AIMessage(content="ok")
```

Apply this substitution to all three `RecordingModel` definitions in the
file (they are locally scoped per test, so each needs its own edit).

- [ ] **Step 2: Run the existing suite to confirm it's still green with the substitution alone**

Run: `uv run pytest tests/test_graph.py -v`
Expected: PASS — this step changes no production code, so every existing
test must still pass exactly as before. If any fail, stop and fix the
substitution before proceeding; do not build the next step on a red suite.

- [ ] **Step 3: Write the new failing tests for the tools loop**

```python
async def test_eve_calls_a_tool_and_returns_the_final_answer(monkeypatch):
    from langchain_core.tools import tool

    @tool
    async def get_widget(name: str) -> str:
        """Look up a widget."""
        return f"widget:{name}"

    tool_call = {
        "name": "get_widget", "args": {"name": "sprocket"}, "id": "call-1", "type": "tool_call",
    }
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._STATIC_TOOLS", [get_widget])

    def factory(_tier):
        return FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="It's a sprocket."),
                ]
            )
        )

    app = build_graph(
        model_factory=factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("what's the widget?")]}, CONFIG)

    assert result["messages"][-1].content == "It's a sprocket."
    tool_message = result["messages"][-2]
    assert tool_message.type == "tool"
    assert tool_message.content == "widget:sprocket"


async def test_a_dynamically_bound_tool_is_callable_the_turn_it_is_discovered(monkeypatch):
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import tool
    from langgraph.types import Command

    spec = {
        "server_id": "mock-server", "tool_name": "roll_dice",
        "description": "Roll a die.", "schema": {"properties": {}},
    }

    @tool
    async def fake_search_skills(query: str, tool_call_id: str) -> Command:
        """stand-in for eve.skills.search.search_skills"""
        return Command(
            update={
                "messages": [ToolMessage("Tool available: roll_dice", tool_call_id=tool_call_id)],
                "dynamic_tools": [spec],
            }
        )

    called_with = {}

    def fake_materialize(spec_):
        @tool
        async def roll_dice() -> str:
            """Roll a die."""
            called_with["invoked"] = True
            return "4"

        return roll_dice

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._STATIC_TOOLS", [fake_search_skills])
    monkeypatch.setattr("eve.graph.materialize", fake_materialize)

    search_call = {
        "name": "fake_search_skills", "args": {"query": "roll a die"},
        "id": "call-1", "type": "tool_call",
    }
    dice_call = {"name": "roll_dice", "args": {}, "id": "call-2", "type": "tool_call"}

    def factory(_tier):
        return FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[search_call]),
                    AIMessage(content="", tool_calls=[dice_call]),
                    AIMessage(content="You rolled a 4."),
                ]
            )
        )

    app = build_graph(
        model_factory=factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("roll a die")]}, CONFIG)

    assert called_with.get("invoked") is True
    assert result["messages"][-1].content == "You rolled a 4."
    assert result["dynamic_tools"] == [spec]
```

- [ ] **Step 4: Run to verify the new tests fail**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL — `eve.graph` has no `_STATIC_TOOLS` attribute yet, and the
`eve` node does not route to a `tools` node.

- [ ] **Step 5: Implement the tools loop**

```python
"""src/eve/graph.py — add these imports and change `eve`, and add the
tools node and conditional edges."""

from langgraph.prebuilt import ToolNode, tools_condition

from eve.memory.search import search_memory
from eve.skills.materialize import materialize
from eve.skills.search import search_skills
from eve.specialists.finances import ask_finances
from eve.specialists.home import ask_home
from eve.specialists.mail import ask_mail

_STATIC_TOOLS = [ask_home, ask_mail, ask_finances, search_skills, search_memory]


def build_graph(
    model_factory=get_model, recall_fn=memory_recall, extract_fn=memory_extract
) -> StateGraph:
    async def eve(state: EveState, config: RunnableConfig) -> dict:
        model = model_factory(Tier.VOICE)
        dynamic = [materialize(spec) for spec in state.get("dynamic_tools", [])]
        bound_model = model.bind_tools([*_STATIC_TOOLS, *dynamic])
        prompt = context.build_system_prompt(
            context.load_persona(), state["member"], state.get("memory")
        )
        messages = [_persona_message(prompt), *state["messages"]]
        return {"messages": [await bound_model.ainvoke(messages, config)]}

    async def tools_node(state: EveState, config: RunnableConfig) -> dict:
        dynamic = [materialize(spec) for spec in state.get("dynamic_tools", [])]
        # Rebuilt fresh, not cached on the module: a spec discovered by
        # search_skills two turns ago must still resolve to a live tool now,
        # and EveState is the only thing that survives between them.
        node = ToolNode([*_STATIC_TOOLS, *dynamic])
        return await node.ainvoke(state, config)

    builder = StateGraph(EveState)
    builder.add_node("load_context", load_context)
    builder.add_node("recall", recall_fn)
    builder.add_node("eve", eve)
    builder.add_node("tools", tools_node)
    builder.add_node("extract", extract_fn)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "recall")
    builder.add_edge("recall", "eve")
    # Bounded by LangGraph's own recursion_limit (default 25), not a custom
    # counter - ponytail: a runaway loop still terminates instead of running
    # forever, and this is the platform mechanism for exactly that ceiling.
    # Raise a dedicated counter only if a real runaway is ever observed.
    builder.add_conditional_edges("eve", tools_condition, {"tools": "tools", END: "extract"})
    builder.add_edge("tools", "eve")
    builder.add_edge("extract", END)
    return builder
```

`tools_condition` inspects the last message in `state["messages"]` and
routes to whichever key its second argument maps `"tools"` to when tool
calls are present, or `END` (mapped here to `"extract"`) otherwise — the
dict argument overrides its default node names to match this graph's own
`"tools"` / `"extract"` names.

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_graph.py -v`
Expected: PASS, all tests including the two new ones.

- [ ] **Step 7: Run the entire unit suite**

Run: `uv run pytest -v`
Expected: PASS. This is the point where every module written in Tasks 1-13
is imported together for the first time — a passing full run here is the
real integration checkpoint before touching `eve-tools`.

- [ ] **Step 8: Commit**

```bash
git add src/eve/graph.py tests/test_graph.py
git commit -m "feat: wire the tools loop into eve's graph"
```

---

## Task 14: `eve-tools` skeleton and the Home Assistant client

**Files:**
- Create: `src/eve_tools/__init__.py` (empty)
- Create: `src/eve_tools/settings.py`
- Create: `src/eve_tools/home_assistant.py`
- Create: `src/eve_tools/app.py`
- Test: `tests/test_eve_tools_home_assistant.py`
- Test: `tests/test_eve_tools_app.py`

**Interfaces:**
- Produces: `eve_tools.settings.get_tools_settings() -> ToolsSettings` (own
  `env_prefix="EVE_TOOLS_"` pydantic-settings model, no relation to
  `eve.settings.Settings`), `eve_tools.home_assistant.get_state(entity_id)`,
  `eve_tools.home_assistant.call_service(domain, service, entity_id, data)`,
  the FastAPI `app` object with `POST /invoke` and `GET /healthz`. Consumed
  by Task 15 (adds `mail`/`finances`/`mcp` handlers to the same `app`) and
  Task 16 (runs `app` as a subprocess for integration tests).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_eve_tools_home_assistant.py"""
import httpx
import pytest
import respx

from eve_tools import home_assistant


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_HOME_ASSISTANT_URL", "http://ha.test")
    monkeypatch.setenv("EVE_TOOLS_HOME_ASSISTANT_TOKEN", "ha-token")


@respx.mock
async def test_get_state_reads_from_home_assistant():
    respx.get("http://ha.test/api/states/light.kitchen").mock(
        return_value=httpx.Response(200, json={"entity_id": "light.kitchen", "state": "on"})
    )
    result = await home_assistant.get_state("light.kitchen")
    assert result["state"] == "on"


@respx.mock
async def test_get_state_sends_the_bearer_token():
    route = respx.get("http://ha.test/api/states/light.kitchen").mock(
        return_value=httpx.Response(200, json={})
    )
    await home_assistant.get_state("light.kitchen")
    assert route.calls.last.request.headers["authorization"] == "Bearer ha-token"


@respx.mock
async def test_call_service_posts_to_home_assistant():
    route = respx.post("http://ha.test/api/services/light/turn_on").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = await home_assistant.call_service(
        "light", "turn_on", "light.kitchen", {"brightness": 200}
    )
    assert result["called"] is True
    body = route.calls.last.request.content
    import json as _json
    assert _json.loads(body) == {"entity_id": "light.kitchen", "brightness": 200}
```

```python
"""tests/test_eve_tools_app.py"""
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from eve_tools.app import app


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_invoke_requires_the_bearer_token():
    async with await _client() as client:
        response = await client.post("/invoke", json={"tool": "home.get_state", "arguments": {}})
    assert response.status_code == 401


async def test_invoke_dispatches_to_the_registered_handler(monkeypatch):
    mock_get_state = AsyncMock(return_value={"state": "on"})
    monkeypatch.setattr("eve_tools.app.home_assistant.get_state", mock_get_state)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "home.get_state", "arguments": {"entity_id": "light.kitchen"}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert response.json() == {"result": {"state": "on"}}
    mock_get_state.assert_awaited_once_with("light.kitchen")


async def test_invoke_returns_404_for_an_unknown_tool():
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "nonexistent.tool", "arguments": {}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 404


async def test_invoke_turns_an_upstream_exception_into_an_error_body(monkeypatch):
    async def _boom(_entity_id):
        raise RuntimeError("Home Assistant unreachable")

    monkeypatch.setattr("eve_tools.app.home_assistant.get_state", _boom)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "home.get_state", "arguments": {"entity_id": "light.kitchen"}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert "Home Assistant unreachable" in response.json()["error"]


async def test_healthz_needs_no_auth():
    async with await _client() as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_eve_tools_home_assistant.py tests/test_eve_tools_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_tools'`

Since `eve_tools` is a second top-level package under `src/`, add
`pythonpath = ["src"]` to `pyproject.toml`'s `[tool.pytest.ini_options]` if
it is not already resolvable — `eve`'s own package is importable today via
`uv sync`'s editable install, which only covers the package matching the
project name (`eve`), not a sibling directory. Verify with
`uv run python -c "import eve_tools"` before running the tests; add the
`pythonpath` line only if that import fails.

- [ ] **Step 3: Implement**

```python
"""src/eve_tools/settings.py

eve-tools' own configuration - deliberately separate from eve.settings.
Settings: this is a different process, holding different (and more
sensitive) secrets, and the two must never share a settings object. The
`api_key` field's env var, EVE_TOOLS_API_KEY, is the one deliberate overlap
- the same literal name eve.settings.Settings.tools_api_key resolves to,
because it is one shared secret between two processes (design doc section
7.1's open item, resolved here as a shared bearer token).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ToolsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVE_TOOLS_", extra="ignore")

    api_key: str = ""
    home_assistant_url: str = ""
    home_assistant_token: str = ""
    gmail_credentials_json: str = ""
    monarch_email: str = ""
    monarch_password: str = ""


@lru_cache(maxsize=1)
def get_tools_settings() -> ToolsSettings:
    return ToolsSettings()
```

```python
"""src/eve_tools/home_assistant.py

Home Assistant REST client. No SDK: the REST API is two calls behind a
long-lived token, and a dependency buys nothing over httpx for that.
"""

from __future__ import annotations

import httpx

from eve_tools.settings import get_tools_settings


async def get_state(entity_id: str) -> dict:
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.home_assistant_url}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {settings.home_assistant_token}"},
        )
        response.raise_for_status()
        return response.json()


async def call_service(domain: str, service: str, entity_id: str, data: dict) -> dict:
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{settings.home_assistant_url}/api/services/{domain}/{service}",
            headers={"Authorization": f"Bearer {settings.home_assistant_token}"},
            json={"entity_id": entity_id, **data},
        )
        response.raise_for_status()
        return {"called": True, "response": response.json()}
```

```python
"""src/eve_tools/app.py

The only third-party-credentialed HTTP surface in the deployment. One route
dispatches by a namespaced tool name; anything not in the table 404s rather
than growing a big if/elif. Every handler's exception becomes {"error": ...}
with a 200 - the caller (eve.tools_client.invoke) already treats a non-2xx
response as a transport failure, and an upstream API error is a normal,
expected outcome here, not a transport failure.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from eve_tools import home_assistant
from eve_tools.settings import get_tools_settings

app = FastAPI()


class InvokeRequest(BaseModel):
    tool: str
    arguments: dict


_HANDLERS = {
    "home.get_state": lambda a: home_assistant.get_state(a["entity_id"]),
    "home.call_service": lambda a: home_assistant.call_service(
        a["domain"], a["service"], a["entity_id"], a.get("data") or {}
    ),
}


def _check_auth(authorization: str | None) -> None:
    settings = get_tools_settings()
    if not settings.api_key or authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/invoke")
async def invoke_tool(
    body: InvokeRequest, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    handler = _HANDLERS.get(body.tool)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"unknown tool {body.tool!r}")
    try:
        result = await handler(body.arguments)
    except Exception as exc:
        return {"error": str(exc)}
    return {"result": result}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_eve_tools_home_assistant.py tests/test_eve_tools_app.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/ tests/test_eve_tools_home_assistant.py tests/test_eve_tools_app.py pyproject.toml
git commit -m "feat: eve-tools skeleton and the Home Assistant client"
```

---

## Task 15: Gmail, Monarch Money, and generic MCP dispatch in `eve-tools`

**Files:**
- Create: `src/eve_tools/gmail.py`
- Create: `src/eve_tools/monarch.py`
- Create: `src/eve_tools/mcp_servers.py`
- Create: `src/eve_tools/mcp_dispatch.py`
- Modify: `src/eve_tools/app.py` (extend `_HANDLERS`)
- Test: `tests/test_eve_tools_gmail.py`
- Test: `tests/test_eve_tools_monarch.py`
- Test: `tests/test_eve_tools_mcp.py`

**Interfaces:**
- Produces: `gmail.list_messages/get_thread/send_email(member_sub, ...)`,
  `monarch.list_transactions/get_budgets(...)`,
  `mcp_servers.register(server_id, params)` /
  `mcp_servers.server_params_for(server_id)`,
  `mcp_dispatch.invoke(server_id, tool_name, arguments) -> dict`. Consumed by
  `app.py`'s handler table (this task) and by Task 18's end-to-end skills
  test.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_eve_tools_gmail.py"""
from unittest.mock import MagicMock, patch

import pytest

from eve_tools import gmail


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv(
        "EVE_TOOLS_GMAIL_CREDENTIALS_JSON",
        '{"sub-noah": {"token": "t", "refresh_token": "r", "client_id": "c", '
        '"client_secret": "s", "token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.modify"]}}',
    )


async def test_list_messages_calls_the_gmail_api():
    fake_service = MagicMock()
    fake_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }
    with patch("eve_tools.gmail._service", return_value=fake_service), \
         patch("eve_tools.gmail._credentials_for", return_value=MagicMock(expired=False)):
        result = await gmail.list_messages("sub-noah", "is:unread")
    assert result == {"messages": [{"id": "m1"}]}


async def test_send_email_builds_a_base64_raw_message():
    fake_service = MagicMock()
    fake_service.users().messages().send().execute.return_value = {"id": "sent-1"}
    with patch("eve_tools.gmail._service", return_value=fake_service):
        result = await gmail.send_email("sub-noah", "a@b.com", "Hi", "Body text")
    assert result == {"sent": True, "id": "sent-1"}
```

```python
"""tests/test_eve_tools_monarch.py"""
from unittest.mock import AsyncMock, patch

import pytest

from eve_tools import monarch


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_MONARCH_EMAIL", "family@example.com")
    monkeypatch.setenv("EVE_TOOLS_MONARCH_PASSWORD", "hunter2")
    monarch._logged_in = False
    monarch._client.cache_clear()


async def test_list_transactions_filters_by_category():
    fake_client = AsyncMock()
    fake_client.login = AsyncMock()
    fake_client.get_transactions = AsyncMock(
        return_value={
            "allTransactions": {
                "results": [
                    {"id": "1", "category": {"name": "Groceries"}},
                    {"id": "2", "category": {"name": "Gas"}},
                ]
            }
        }
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.list_transactions(limit=20, category="Groceries")
    assert [t["id"] for t in result["transactions"]] == ["1"]
    fake_client.login.assert_awaited_once_with(email="family@example.com", password="hunter2")
```

```python
"""tests/test_eve_tools_mcp.py

Exercises the generic MCP dispatcher against a real local MCP server run
over stdio - the "local mock server" the design doc calls for (section 2.1's
non-goal: no live MCP server ships this phase, but the plumbing is real).
"""
import sys

import pytest

from eve_tools import mcp_dispatch, mcp_servers

MOCK_SERVER_SCRIPT = """
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("mock-server")

@server.list_tools()
async def list_tools():
    return [Tool(name="roll_dice", description="Roll a die.", inputSchema={"type": "object", "properties": {}})]

@server.call_tool()
async def call_tool(name, arguments):
    return [TextContent(type="text", text="4")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

asyncio.run(main())
"""


@pytest.fixture
def mock_server_script(tmp_path):
    script = tmp_path / "mock_mcp_server.py"
    script.write_text(MOCK_SERVER_SCRIPT)
    return script


async def test_dispatch_invokes_a_real_local_mcp_server(mock_server_script):
    from mcp import StdioServerParameters

    mcp_servers.register(
        "mock-server", StdioServerParameters(command=sys.executable, args=[str(mock_server_script)])
    )
    result = await mcp_dispatch.invoke("mock-server", "roll_dice", {})
    assert result["content"][0]["text"] == "4"


async def test_dispatch_raises_for_an_unregistered_server():
    with pytest.raises(KeyError):
        await mcp_dispatch.invoke("nonexistent-server", "anything", {})
```

Mark this file `@pytest.mark.integration` at the top with
`pytestmark = pytest.mark.integration` — it spawns a real subprocess over
stdio, which is heavier than a pure unit test even though it needs no
Docker service.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_eve_tools_gmail.py tests/test_eve_tools_monarch.py -v`
Expected: FAIL with `ModuleNotFoundError`.
Run: `uv run pytest -m integration tests/test_eve_tools_mcp.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""src/eve_tools/gmail.py

Gmail client via the official googleapiclient, OAuth refreshed from a
stored token. One credential per family member: gmail_credentials_json
holds a JSON object keyed by member sub, each value the shape
google.oauth2.credentials.Credentials.to_authorized_user_info() produces
(obtained via scripts/gmail_oauth_setup.py, Task 17).

googleapiclient is synchronous; every call here runs in a thread via
asyncio.to_thread so it does not block eve-tools' event loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from eve_tools.settings import get_tools_settings

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _credentials_for(member_sub: str) -> Credentials:
    all_creds = json.loads(get_tools_settings().gmail_credentials_json or "{}")
    creds = Credentials.from_authorized_user_info(all_creds[member_sub], _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _service(member_sub: str):
    return build("gmail", "v1", credentials=_credentials_for(member_sub))


async def list_messages(member_sub: str, query: str) -> dict:
    def _run():
        service = _service(member_sub)
        return service.users().messages().list(userId="me", q=query, maxResults=10).execute()

    return await asyncio.to_thread(_run)


async def get_thread(member_sub: str, thread_id: str) -> dict:
    def _run():
        service = _service(member_sub)
        return service.users().threads().get(userId="me", id=thread_id).execute()

    return await asyncio.to_thread(_run)


async def send_email(member_sub: str, to: str, subject: str, body: str) -> dict:
    def _run():
        service = _service(member_sub)
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {"sent": True, "id": sent["id"]}

    return await asyncio.to_thread(_run)
```

The unit test patches `_service` directly, so it does not exercise the
`asyncio.to_thread` wrapping — that's acceptable here since `to_thread` is a
stdlib primitive with no branching logic of its own to test; what needs
verification is that the right googleapiclient calls happen with the right
arguments, which the test already checks.

```python
"""src/eve_tools/monarch.py

Monarch Money client via the community-maintained `monarchmoney` package -
Monarch has no public documented API, and reverse-engineering its GraphQL
auth flow by hand would be a second maintenance burden for no benefit over
a client that already handles session persistence.

MFA is not handled here: the Monarch account this deployment uses should
either have MFA disabled or a persisted session provisioned out-of-band
(Task 17's provisioning note). A homelab-scale, five-person deployment does
not need an interactive MFA prompt mid-conversation.
"""

from __future__ import annotations

from functools import lru_cache

from monarchmoney import MonarchMoney

from eve_tools.settings import get_tools_settings

_logged_in = False


@lru_cache(maxsize=1)
def _client() -> MonarchMoney:
    return MonarchMoney()


async def _authenticated() -> MonarchMoney:
    global _logged_in
    client = _client()
    if not _logged_in:
        settings = get_tools_settings()
        await client.login(email=settings.monarch_email, password=settings.monarch_password)
        _logged_in = True
    return client


async def list_transactions(limit: int, category: str | None) -> dict:
    client = await _authenticated()
    result = await client.get_transactions(limit=limit)
    transactions = result.get("allTransactions", {}).get("results", [])
    if category:
        transactions = [
            t for t in transactions if (t.get("category") or {}).get("name") == category
        ]
    return {"transactions": transactions}


async def get_budgets() -> dict:
    client = await _authenticated()
    return await client.get_budgets()
```

```python
"""src/eve_tools/mcp_servers.py

Registered MCP servers, by id. Empty in production until a concrete skill
needs one (design doc section 2.1's non-goal) - populated in tests with a
local mock server's connection parameters.
"""

from __future__ import annotations

from mcp import StdioServerParameters

_SERVERS: dict[str, StdioServerParameters] = {}


def register(server_id: str, params: StdioServerParameters) -> None:
    _SERVERS[server_id] = params


def server_params_for(server_id: str) -> StdioServerParameters:
    if server_id not in _SERVERS:
        raise KeyError(f"no MCP server registered as {server_id!r}")
    return _SERVERS[server_id]
```

```python
"""src/eve_tools/mcp_dispatch.py

Generic dispatcher for dynamically-discovered MCP tools. A fresh connection
per call, not a kept-open session - EveState may checkpoint across process
restarts, and this side is only ever handed a server id and tool name, never
a live session (design doc section 5.1's constraint, mirrored here for
symmetry).
"""

from __future__ import annotations

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from eve_tools.mcp_servers import server_params_for


async def invoke(server_id: str, tool_name: str, arguments: dict) -> dict:
    params = server_params_for(server_id)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return {"content": [c.model_dump() for c in result.content]}
```

Extend `src/eve_tools/app.py`'s imports and `_HANDLERS`:

```python
from eve_tools import gmail, home_assistant, mcp_dispatch, monarch

_HANDLERS = {
    "home.get_state": lambda a: home_assistant.get_state(a["entity_id"]),
    "home.call_service": lambda a: home_assistant.call_service(
        a["domain"], a["service"], a["entity_id"], a.get("data") or {}
    ),
    "mail.list_messages": lambda a: gmail.list_messages(a["member_sub"], a["query"]),
    "mail.get_thread": lambda a: gmail.get_thread(a["member_sub"], a["thread_id"]),
    "mail.send_email": lambda a: gmail.send_email(
        a["member_sub"], a["to"], a["subject"], a["body"]
    ),
    "finances.list_transactions": lambda a: monarch.list_transactions(
        a.get("limit", 20), a.get("category")
    ),
    "finances.get_budgets": lambda a: monarch.get_budgets(),
    "mcp.invoke": lambda a: mcp_dispatch.invoke(
        a["server_id"], a["tool_name"], a["arguments"]
    ),
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_eve_tools_gmail.py tests/test_eve_tools_monarch.py tests/test_eve_tools_app.py -v`
Expected: PASS.
Run: `uv run pytest -m integration tests/test_eve_tools_mcp.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/gmail.py src/eve_tools/monarch.py src/eve_tools/mcp_servers.py \
        src/eve_tools/mcp_dispatch.py src/eve_tools/app.py \
        tests/test_eve_tools_gmail.py tests/test_eve_tools_monarch.py tests/test_eve_tools_mcp.py
git commit -m "feat: Gmail, Monarch Money, and generic MCP dispatch in eve-tools"
```

---

## Task 16: `eve-tools`' Dockerfile and an integration test across the real HTTP boundary

Mirrors the existing `aegra_server` fixture convention in `tests/conftest.py`
exactly: spawn the real process locally against real (or stub) backing
services, rather than inventing a new Docker-based test harness.

**Files:**
- Create: `Dockerfile.eve-tools`
- Create: `tests/fixtures/stub_home_assistant.py`
- Modify: `tests/conftest.py` (add `eve_tools_server` fixture)
- Test: `tests/test_specialists_integration.py`

**Interfaces:**
- Produces: a runnable `eve-tools` container image; a `stub_home_assistant`
  pytest fixture (in-process FastAPI app on a background thread); an
  `eve_tools_server` pytest fixture (real `eve-tools` subprocess). Consumed
  by this task's own integration test and available to Task 18/19.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# Dockerfile.eve-tools
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv

WORKDIR /app

# --no-install-project: eve-tools is never "the project" uv_build knows how
# to package (that's "eve", src/eve) - this installs only the dependency
# tree from the shared lockfile, and PYTHONPATH below makes src/eve_tools
# importable without needing it to be.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/eve_tools ./src/eve_tools

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PORT=8090

EXPOSE 8090

RUN useradd --system --uid 10002 --no-create-home eve-tools \
    && chown -R eve-tools:eve-tools /app
USER 10002

CMD ["uvicorn", "eve_tools.app:app", "--host", "0.0.0.0", "--port", "8090"]
```

Verify the image builds and serves before proceeding:

```bash
docker build -f Dockerfile.eve-tools -t eve-tools:test .
docker run --rm -p 18090:8090 -e EVE_TOOLS_API_KEY=test-key eve-tools:test &
sleep 2
curl -s http://127.0.0.1:18090/healthz
docker stop $(docker ps -q --filter ancestor=eve-tools:test)
```

Expected: `{"status":"ok"}`.

- [ ] **Step 2: Add the stub Home Assistant fixture**

```python
"""tests/fixtures/stub_home_assistant.py

A minimal stand-in for Home Assistant's REST API, for integration tests
that exercise the real HTTP boundary to eve-tools without touching the real
home lab instance.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()
_states = {"light.kitchen": "off"}


@app.get("/api/states/{entity_id}")
async def get_state(entity_id: str) -> dict:
    return {"entity_id": entity_id, "state": _states.get(entity_id, "unknown")}


@app.post("/api/services/{domain}/{service}")
async def call_service(domain: str, service: str, body: dict) -> list:
    _states[body["entity_id"]] = "on" if service == "turn_on" else "off"
    return []
```

- [ ] **Step 3: Add `conftest.py` fixtures spawning the stub and `eve-tools` itself**

```python
"""Additions to tests/conftest.py"""
import threading

import uvicorn

EVE_TOOLS_URL = "http://127.0.0.1:18090"


@pytest.fixture(scope="session")
def stub_home_assistant():
    from tests.fixtures.stub_home_assistant import app as stub_app

    config = uvicorn.Config(stub_app, host="127.0.0.1", port=18091, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get("http://127.0.0.1:18091/api/states/x", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError("stub Home Assistant did not start")
    yield "http://127.0.0.1:18091"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def eve_tools_server(stub_home_assistant):
    env = {
        **os.environ,
        "EVE_TOOLS_API_KEY": "test-key",
        "EVE_TOOLS_HOME_ASSISTANT_URL": stub_home_assistant,
        "EVE_TOOLS_HOME_ASSISTANT_TOKEN": "unused-in-stub",
    }
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "eve_tools.app:app", "--host", "127.0.0.1", "--port", "18090"],
        env=env,
        start_new_session=True,
    )

    def _terminate():
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"{EVE_TOOLS_URL}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        _terminate()
        raise RuntimeError("eve-tools did not become ready within 20s")
    yield EVE_TOOLS_URL
    _terminate()
```

- [ ] **Step 4: Write the failing integration test**

```python
"""tests/test_specialists_integration.py"""
import pytest

from eve.specialists.home import ask_home

pytestmark = pytest.mark.integration


async def test_ask_home_reads_real_state_through_a_running_eve_tools(
    eve_tools_server, monkeypatch
):
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", eve_tools_server)
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")
    from eve.settings import get_settings

    get_settings.cache_clear()

    state = {
        "member": {
            "sub": "sub-noah", "name": "Noah", "role": "adult",
            "timezone": "America/Vancouver", "permissions": ["home.control"],
            "local_time": "2026-08-21 09:00 PDT",
        },
        "messages": [], "system_prompt": "", "memory": None, "dynamic_tools": [],
    }
    result = await ask_home.ainvoke(
        {"request": "is the kitchen light on?", "state": state, "config": {"configurable": {}}}
    )
    assert "off" in result.lower()
```

This exercises the tool-calling loop against the real `ask_home` model
call too, so it needs a real (or fake, via monkeypatching
`eve.specialists.home._model_for_test`) tool-calling model — reuse the
`FakeToolCallingModel` substitution pattern from Task 5 rather than spending
live LiteLLM quota in an integration test. Add that monkeypatch before
running.

- [ ] **Step 5: Run to verify it fails, then passes**

Run: `uv run pytest -m integration tests/test_specialists_integration.py -v`
Expected: FAIL first (no `eve_tools_server` fixture / wiring), then PASS
once Steps 2-3 are in place.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.eve-tools tests/fixtures/stub_home_assistant.py tests/conftest.py \
        tests/test_specialists_integration.py
git commit -m "feat: eve-tools Dockerfile and a real-HTTP-boundary integration test"
```

---

## Task 17: Gmail and Monarch Money credential provisioning

These are manual, interactive steps (OAuth consent requires a browser; the
plan cannot automate obtaining Noah's and Kendra's own consent) — this task
delivers a runnable script for Gmail and a documented runbook step for
Monarch, both landing the result in Vault, matching Prerequisites P1/P2 in
the design doc.

**Files:**
- Create: `scripts/gmail_oauth_setup.py`

**Interfaces:**
- Produces: a script whose output is copy-pasted into
  `kv/credentials/gmail` (or another agreed Vault path) as
  `EVE_TOOLS_GMAIL_CREDENTIALS_JSON`, keyed by each member's Authentik `sub`
  from `family.yaml`.

- [ ] **Step 1: Write the script**

```python
"""scripts/gmail_oauth_setup.py

Run locally, once per family member, to obtain a Gmail OAuth refresh token.
Requires a Google Cloud OAuth client (Desktop app type) with the Gmail API
enabled - create one at https://console.cloud.google.com/apis/credentials
and download its client secret JSON alongside this script as
`client_secret.json` before running.

Usage: uv run python scripts/gmail_oauth_setup.py <member-sub>

Prints the authorized_user JSON for that member. Merge it into the
EVE_TOOLS_GMAIL_CREDENTIALS_JSON blob in Vault (kv/credentials/gmail),
keyed by the member sub passed on the command line - see family.yaml for
the sub values.
"""

from __future__ import annotations

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    member_sub = sys.argv[1]

    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", _SCOPES)
    credentials = flow.run_local_server(port=0)
    print(f"\n--- credentials for {member_sub} ---")
    print(json.dumps(json.loads(credentials.to_json())))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it once per member (manual, not automatable)**

```bash
uv run python scripts/gmail_oauth_setup.py a06dc93aea7f4d4116e550f9c826fc59b7c36f083a3a19807bab5290e12d00cb  # Noah
uv run python scripts/gmail_oauth_setup.py b96297cfe2cd39700a9d394e99cb98cb4c84167caccae5c6ab596a17a799495c  # Kendra
```

Merge both outputs into one JSON object keyed by sub, and store it in Vault
as `kv/credentials/gmail`, field `EVE_TOOLS_GMAIL_CREDENTIALS_JSON` — wired
into the `eve-tools` Deployment's `ExternalSecret` at deploy time (Task 19).

- [ ] **Step 3: Monarch Money credentials (no script — just a Vault entry)**

Store `EVE_TOOLS_MONARCH_EMAIL` and `EVE_TOOLS_MONARCH_PASSWORD` in Vault as
`kv/credentials/monarch`, using an account with MFA disabled (see
`src/eve_tools/monarch.py`'s module docstring, Task 15, for why).

- [ ] **Step 4: Commit**

```bash
git add scripts/gmail_oauth_setup.py
git commit -m "feat: Gmail OAuth provisioning script (P1)"
```

---

## Task 18: A real example skill, end to end

Proves `search_skills` finds a `SKILL.md` procedure and that a dynamically
discovered MCP tool survives to a second turn — the two claims in the
design doc's definition of done (items 6 and 7).

**Files:**
- Create: `skills/greet-warmly/SKILL.md`
- Test: `tests/test_skills_integration.py`

**Interfaces:**
- Consumes: everything from Tasks 8-13 and 15-16.
- Produces: nothing new — this is a verification task.

- [ ] **Step 1: Write the example skill**

```markdown
<!-- skills/greet-warmly/SKILL.md -->
---
name: greet-warmly
description: How to greet a family member warmly at the start of a conversation.
---
Use their first name. Ask one specific, genuine question about their day
rather than a generic "how are you" - reference something you already know
about their schedule or plans if you have it in memory.
```

- [ ] **Step 2: Write the failing tests**

```python
"""tests/test_skills_integration.py"""
import pytest

from eve.skills.registry import load_skills
from eve.skills.search import rank_skills
from eve.settings import get_settings


def test_the_example_skill_loads_from_disk():
    get_settings.cache_clear()
    skills = load_skills()
    names = [s.name for s in skills]
    assert "greet-warmly" in names


async def test_search_skills_finds_the_example_skill_for_a_relevant_query():
    get_settings.cache_clear()
    skills = load_skills()
    ranked = await rank_skills("how should I say hello", skills, top_k=1)
    assert ranked[0].name == "greet-warmly"
```

The second test makes a real embedding call (`eve.memory.embed.embed_query`
via `rank_skills`) — mark it `@pytest.mark.live` and skip it unless
`EVE_LIVE_TESTS=1`, matching every other test that spends real LiteLLM quota:

```python
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("EVE_LIVE_TESTS") != "1",
        reason="set EVE_LIVE_TESTS=1 to run against the real embedding model",
    ),
]
```

Add `import os` and this `pytestmark` before the two test functions; keep
`test_the_example_skill_loads_from_disk` un-gated since it makes no network
call.

- [ ] **Step 3: Run to verify the disk-loading test passes immediately and the live test is skipped by default**

Run: `uv run pytest tests/test_skills_integration.py -v`
Expected: `test_the_example_skill_loads_from_disk` PASSES;
`test_search_skills_finds_the_example_skill_for_a_relevant_query` SKIPPED.

- [ ] **Step 4: Run the live test once, with real credentials, to confirm end-to-end ranking works**

Run: `EVE_LIVE_TESTS=1 uv run pytest tests/test_skills_integration.py -v -m live`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/greet-warmly/SKILL.md tests/test_skills_integration.py
git commit -m "feat: an example skill, proving search_skills end to end"
```

---

## Task 19: Live re-probes, `docs/architecture.md`, and ADR 0006

**Files:**
- Modify: `tests/test_live_models.py`
- Modify: `docs/architecture.md`
- Create: `docs/adr/0006-eve-tools-isolation.md`

**Interfaces:** none — this is the closing verification and documentation
task.

- [ ] **Step 1: Add the MECHANICAL-tier tool-calling re-probe**

`test_voice_tier_emits_tool_calls` already exists and covers `Tier.VOICE`
(its own comment: "Phase 3's entire topology depends on this working").
Design doc definition-of-done item 8 calls out `Tier.MECHANICAL`
specifically, since specialists run on it and it was never probed for tool
calling after the `gpt-5.6-*` rename (only `gpt-5.4` was, per ADR 0004).
Add, right after `test_voice_tier_emits_tool_calls`:

```python
async def test_mechanical_tier_emits_tool_calls():
    # ADR 0004 probed gpt-5.4 for tool calling before the gpt-5.6-* rename;
    # gpt-5.6-luna (MECHANICAL) was never confirmed live. Every specialist
    # (design doc section 4) depends on this.
    model = get_model(Tier.MECHANICAL).bind_tools([get_weather])
    reply = await model.ainvoke([HumanMessage("What is the weather in Toronto?")])
    assert reply.tool_calls, "proxy did not return tool calls for MECHANICAL tier"
    assert reply.tool_calls[0]["name"] == "get_weather"
```

- [ ] **Step 2: Run it live**

Run: `EVE_LIVE_TESTS=1 uv run pytest tests/test_live_models.py -v -m live`
Expected: PASS. If it fails, this is exactly the kind of finding ADR 0004
recorded before — stop and record a new finding in that ADR rather than
proceeding to wire specialists into a production deployment on a tier that
cannot call tools.

- [ ] **Step 3: Write ADR 0006**

Design doc section 14 flagged this as a "candidate ADR 0006," deliberately
deferred to implementation time rather than written speculatively at design
time — matching how Phase 2's own ADR amendments (0002, 0003) were finalized
post-implementation. Follow the existing ADR format exactly
(`docs/adr/0001-agents-as-subgraph-tools.md`'s Status/Context/Decision/
Consequences structure):

```markdown
# 6. Specialist and skill tool execution runs in an isolated service

**Status:** Accepted
**Date:** 2026-08-21

## Context

Specialists and dynamically-discovered skills hold real third-party
credentials (Home Assistant, Gmail, Monarch Money) and take real-world
actions. ADR 0001 kept specialist *reasoning* in Eve's own process for
latency and tracing reasons. Whether specialist *tool execution* should
live there too is a separate question, and the risk profile differs: a
credential-holding leaf call is exactly the kind of thing whose blast
radius matters if a call goes wrong or a dependency is compromised.

## Decision

Every specialist tool and the generic MCP dispatcher call out to
`eve-tools`, a separate long-running service holding every third-party
credential, no family or permission data, and no Kubernetes credentials of
its own. `NetworkPolicy` restricts its egress to exactly the external
hosts it needs. Permission checks happen in Eve's main container, before
the HTTP call, so a denied request never reaches `eve-tools` at all.

## Consequences

A misbehaving or compromised tool call can reach at most the three
external credentials `eve-tools` holds — not the cluster, not family data,
not the other credentials Eve's main container has (LiteLLM, the database).
This refines ADR 0001 rather than reversing it: the rejection of a network
hop there was about the specialist *reasoning loop*, which still runs
in-process; a leaf tool call's HTTP hop to `eve-tools` lands after the
first streamed token and inside the same Langfuse trace either way, so
neither of ADR 0001's original objections applies to it. "One deploy"
becomes two: `eve-ai` and `eve-tools`, both built from this repository.
```

- [ ] **Step 4: Update `docs/architecture.md`**

The file currently opens with "This document describes what exists in this
repository today: Phase 2, 'Memory.'" Replace that paragraph and the graph
diagram to describe Phase 3's shape, following the same structure the
existing Phase 2 update used for Phase 1's sections — update the opening
paragraph's phase name and spec links, the graph diagram
(`START -> load_context -> recall -> eve <-> tools -> extract -> END`), and
add a "Specialists and skills" section mirroring the existing "Module map"
section's level of detail, listing `src/eve/specialists/`, `src/eve/skills/`,
`src/eve/tools_client.py`, and `src/eve_tools/` with one sentence each.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_models.py docs/architecture.md docs/adr/0006-eve-tools-isolation.md
git commit -m "test: re-probe MECHANICAL tier tool calling; docs: Phase 3 architecture and ADR 0006"
```

---

## Definition of done (carried from the design doc, section 13)

| # | Criterion | Verified by |
|---|---|---|
| 1 | Eve turns a real Home Assistant device on or off in one turn | Task 16 (stub) + live manual check against `vm105` |
| 2 | Eve reads and sends real Gmail, gated by `mail.read`/`mail.send` | Task 6 (gating) + Task 17 (credentials) + a live test to add once P1 lands |
| 3 | Eve answers a real financial question from Monarch Money | Task 7 + Task 17 (credentials) + a live test to add once P2 lands |
| 4 | A denied permission gets a graceful explanation, never a graph error | Task 3, Task 4, Task 6 |
| 5 | `eve-tools` holds no family/permission data, no cluster credentials, no Ingress | Task 14-16 (code); Ingress absence is a deployment-manifest fact for the infrastructure repo, out of this plan's scope |
| 6 | `search_skills` finds an authored SKILL.md procedure and Eve follows it | Task 18 |
| 7 | A dynamically-discovered MCP tool is callable the turn it's found, and later in the same thread | Task 13's second graph test |
| 8 | `gpt-5.6-luna`'s tool-calling is reconfirmed live | Task 19 |

Live Gmail and Monarch Money tests are intentionally not written until
Task 17's credentials exist — writing them against non-existent credentials
would either be a placeholder or a test permanently skipped, neither of
which belongs in this plan. Add them as a short follow-up once P1/P2 close,
using `test_live_models.py`'s existing `EVE_LIVE_TESTS=1` gating as the
template.
