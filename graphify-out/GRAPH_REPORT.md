# Graph Report - eve-ai  (2026-09-18)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 4087 nodes · 8169 edges · 263 communities (193 shown, 70 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 983 edges (avg confidence: 0.89)
- Token cost: 23,624 input · 3,174 output

## Graph Freshness
- Built from commit: `7555d770`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Memory Management
- Signal Processing Fixes
- Model Hygiene Rules
- Ambient Notification Mocks
- Wardrobe Catalog Tracing
- Ambient App Testing
- LangGraph Task Dispatch
- ACP API Testing
- Coding Task Delegation
- Graph Route Logic
- Evaluation Gate Logic
- OAuth Token Store
- Memory Extraction Operations
- Calendar Signal Sources
- Computer Settings Configuration
- Git Repository Operations
- Monarch Money Integration
- Member Context Building
- Message History Content
- Coding Session Store
- Agent Client Protocol
- ACP Session Management
- Coding Supervisor Decisions
- Ambient Pipeline Logic
- Evaluation CLI Tool
- Computer Task API
- Database Migrations
- Financial Specialist Testing
- Gmail OAuth Tooling
- Evaluation Scoring Metrics
- Tools API Surface
- Coding Model Dispatch
- Tool Error Handling
- Calendar Source Testing
- Finance Source Testing
- CalDAV Client Testing
- Ambient Notification Store
- Decision Retention Policy
- Ambient Polling App
- Graph Capability Testing
- UI Schema Projection
- Tool Authoring CLI
- Computer Signal Testing
- Personal Access Tokens
- In-Memory Task State
- Memory Rule Operations
- Ambient Filter Testing
- Mail Source Testing
- Langfuse Evaluation Replay
- Tools Client Testing
- Skill Authoring Logic
- End-to-End Integration Tests
- OAuth Token Refresher
- UI Wire Protocol
- Whoop Client Testing
- Dynamic UI Testing
- AST Tool Inspection
- Sandbox Tool Execution
- UI Action Recognition
- Dynamic UI Surface
- Health Provider Integration
- Response Chip Generation
- Thread Title Generation
- Context Loading Node
- Memory Integration Tests
- Family Roster Management
- Skill Serialization Logic
- UI Persistence Logic
- Memory Task Concurrency
- Memory Recall Testing
- UI Stream Handshake
- Database Migration CLI
- Authentication Error Handling
- Whoop Data Normalization
- Specialist Agent Testing
- Notification Delivery Pipeline
- Ambient Audience Filtering
- Live Ambient Integration
- Memory Extraction Prompting
- Skills Registry Loader
- Pipeline Eval Stubs
- Memory Erasure Guards
- Postgres Memory Store
- MCP Tool Registry
- JWT Auth Testing
- Immich Tool Testing
- Shared Test Fixtures
- Ntfy Notification Provider
- Computer Task Store
- Memory Conflict Resolution
- Home Assistant Tools
- Ambient State Markers
- PAT Authentication Logic
- Oura Client Testing
- Gmail API Client
- Resource Scoping Auth
- Sandbox HTTP API
- PAT Management CLI
- Oura API Client
- Wardrobe Catalog Sync
- Coding Dispatch Tests
- Docker Image Integration
- Vector Embedding Tests
- Suggestion Node Tests
- CalDAV Calendar Client
- OAuth Provisioning Server
- Path Confinement Security
- Signal Filter Pipeline
- Model Catalogue Tests
- Tool Dispatch Routing
- UI Schema Validation
- Claude Computer Use
- Gmail OAuth Setup
- UI Action Protocol
- Ambient Integration Tests
- Agent Registry Management
- Ambient Turn Guards
- Tool Approval Store
- Memory Graph Execution
- Member Data Isolation
- Empty Thread Openers
- UI Component State
- Home Assistant Integration
- Vector Database Queries
- Health Provider Fan-out
- Vision Analysis Model
- Live Computer Environment
- Recall Token Budgets
- Memory Search Integration
- Health Specialist Tests
- Monarch Money Client
- Ambient Dataset Evaluation
- Specialist Skill Scoping
- Coding Lifecycle Integration
- Gmail API Integration
- Turn Extraction Stamping
- Embedding Budget Management
- Skill Revision Integration
- Wardrobe Specialist Tests
- Sandbox Security Integration
- UI Protocol Streaming
- MCP Tool Dispatcher
- Wardrobe Asset Store
- Subprocess Execution Wrapper
- Notification Quiet Hours
- Signal Delivery Pipeline
- Computer Task Polling
- Ambient Coding Sessions
- Computer Status Poller
- Sandbox Live Security
- Suggestion Model Fakes
- UI Surface Assembly
- Thread Access Control
- Image Skill Corpus
- Family Roster Management
- Household Tool Definitions
- UI Component Schema
- Memory Registry Fixtures
- Sandbox Docker Image
- MCP Server Registry
- Filter Infrastructure Errors
- Home Assistant Stub
- Relevance Filter Logic
- Specialist Search Builder
- Ambient Filter Verdicts
- Model Provider Config
- Skill Ranking Logic
- OAuth Token Store
- Dynamic Tool Binding
- Detached Span Tracing
- Skill Management CLI
- Wardrobe Management CLI
- Health Metric Tools
- Computer Signal Routing
- Tool Loop Budget
- Skill Documentation Validation
- Sandbox Child Process
- Graph Replay Utilities
- Materialized Tool Calls
- Ambient Source Registry
- Thread Digest Summarization
- Gmail Search Tools
- Procedure Extraction Guards
- Async Extraction Returns
- Background Extraction Switch
- Reflex Tier Streaming
- Database Migration Graph
- Ntfy Notification Delivery
- Malformed Filter Handling
- Turn Failure Resilience
- Opener Memory Bundling
- Sync Verification Script
- Process Reaper Logic
- Notice Delivery Resilience
- Judge Response Validation
- OAuth Token Retrieval
- Recall Cancellation Handling
- Extraction Sequence Invariants
- Project Documentation
- Test Infrastructure Services
- Search Window Logic
- Environment Bootstrap Scripts
- Time Window Testing
- Authentication Fixtures
- AST Security Integration
- Subprocess Error Handling
- Image Publication Scripts
- Tool Execution Sandbox
- Tool Authoring Phase
- Roster Management Fixtures
- Configuration Error Handling
- Timezone Cap Logic
- Permission Gate Mapping
- Notification Idempotency
- Database Connection Pool
- Row Supersession Logic
- Data Deletion Logic
- Salience Reinforcement Logic
- Alembic Migration Versioning
- Memory Eviction Policy
- Eviction Recovery Logic
- Idempotent Eviction Guard
- Supersede API Validation
- Database Migration Idempotency
- Rule Loading Configuration
- Procedure Loading Logic
- Partial Index Correctness
- Memory Limit Enforcement
- Execution Budget Management
- Ambient Turn Logic
- Tool Settings Fixtures
- Surface Parser Matching
- Frame Content Inversion
- Bare Frame Stripping
- Greedy Frame Parsing
- Frame-free Content Handling
- Conservative Frame Stripping
- Frame Builder Logic
- Grid Column Validation
- Action ID Validation
- Release Documentation
- Tool Isolation ADR
- Ambient Impersonation ADR
- Eval Data ADR
- Sandboxed Functions ADR
- Alembic Versioning ADR
- Authored Surfaces ADR
- Thread-free Openers ADR
- Phase 1 Plan
- Phase 2 Plan
- Phase 3 Plan
- Specialist Factory Task
- Phase 4 Plan
- Phase 5b Plan
- Phase 5c Plan
- Core Eve Module

## God Nodes (most connected - your core abstractions)
1. `get_settings()` - 108 edges
2. `Signal` - 67 edges
3. `get_pool()` - 63 edges
4. `Family` - 55 edges
5. `Memory` - 51 edges
6. `Tier` - 50 edges
7. `FilterVerdict` - 44 edges
8. `FakeToolCallingModel` - 43 edges
9. `invoke()` - 43 edges
10. `get_model()` - 42 edges

## Surprising Connections (you probably didn't know these)
- `test_memory_carries_optional_source_thread_and_run()` --uses--> `Memory`  [INFERRED]
  tests/test_state.py → src/eve/memory/types.py
- `test_a_home_signal_is_household_scoped()` --uses--> `Signal`  [INFERRED]
  tests/test_ambient_app.py → src/eve_ambient/types.py
- `test_the_signals_own_cooldown_overrides_the_default()` --uses--> `Signal`  [INFERRED]
  tests/test_ambient_pipeline.py → src/eve_ambient/types.py
- `test_an_unknown_agent_is_a_400_not_a_500()` --uses--> `UnknownAgent`  [INFERRED]
  tests/test_acp_app.py → src/eve_computer/acp/registry.py
- `test_malformed_json_raises_rather_than_reporting_nothing()` --uses--> `SourceUnavailable`  [INFERRED]
  tests/test_ambient_sources_mail.py → src/eve_ambient/types.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Eve Evolution Phases** — docs_superpowers_plans_2026_08_17_eve_core, docs_superpowers_plans_2026_08_18_eve_memory, docs_superpowers_plans_2026_08_21_eve_specialists [EXTRACTED 1.00]

## Communities (263 total, 70 thin omitted)

### Community 0 - "Memory Management"
Cohesion: 0.05
Nodes (80): math, OpenAIEmbeddings, One recall, at creation, snapshotted onto the row for every later supervisor…, _recall_context(), _cmd_hygiene(), apply_duplicates(), find_dead(), Supersede each loser by its keeper. Returns how many were retired. (+72 more)

### Community 1 - "Signal Processing Fixes"
Cohesion: 0.05
Nodes (38): Signal, _fake_source(), _poll(), The other direction of fix round 4 item 1: a deployment with ambient enabled…, A month of calendar entries must not become a month of notifications., An empty inbox, no transactions yet, nothing in the calendar window: the first…, (fix wave item 1) Priming exists to swallow a pre-existing backlog the first…, (fix round 4, item 2)… (+30 more)

### Community 2 - "Model Hygiene Rules"
Cohesion: 0.06
Nodes (56): BaseChatModel, langchain_agents, langchain_core_language_models, langchain_openai, langgraph_errors, Rule-set hygiene: redundant, conflicting, or dormant. Operates on Eve's own…, Report only. Never applied - see the module docstring., report_contradictions() (+48 more)

### Community 3 - "Ambient Notification Mocks"
Cohesion: 0.07
Nodes (55): ambient_settings(), FakeClient, FakeRuns, FakeThreads, fixture, parametrize, RaisingNotifier, Aegra scopes threads to the authenticated identity, so this header is the whole… (+47 more)

### Community 4 - "Wardrobe Catalog Tracing"
Cohesion: 0.07
Nodes (39): start_as_current_span(), _fake_invoke(), FakeSpan, FakeTracer, _item(), _patch_common(), _insert(), Stands in for eve.tools_client.invoke, which returns a JSON STRING. (+31 more)

### Community 5 - "Ambient App Testing"
Cohesion: 0.05
Nodes (34): _clear_background_tasks(), client(), Exception, fixture, Raised by a patched `asyncio.sleep` to end an otherwise-infinite…, (fix round 4, item 11) An operator otherwise cannot tell a wrong secret from an…, (fix round 4, item 1) The autouse `settings` fixture sets…, (fix round 4, item 4) By design no `eve_ambient_seen` row exists until a signal… (+26 more)

### Community 6 - "LangGraph Task Dispatch"
Cohesion: 0.09
Nodes (35): langchain_core_messages, langchain_core_runnables, langchain_core_tools, langgraph_graph, langgraph_graph_message, langgraph_prebuilt, langgraph_types, logging (+27 more)

### Community 7 - "ACP API Testing"
Cohesion: 0.04
Nodes (26): fastapi_testclient, client(), _key(), fixture, The door, not the machinery. `session` is mocked wholesale: Task 4 owns the…, The GUI lane's single worker is the whole reason it exists. A change that lets…, test_an_unknown_agent_is_a_400_not_a_500(), test_the_gui_task_queue_is_untouched() (+18 more)

### Community 8 - "Coding Task Delegation"
Cohesion: 0.06
Nodes (44): check_coding_session(), delegate_coding_task(), RunnableConfig, tool, What Eve's delegated coding sessions are doing right now. Use when a member…, Pass a member's message into a running coding session - a correction, a…, Delegate a coding task to a dedicated coding agent working in a real git…, send_to_coding_session() (+36 more)

### Community 9 - "Graph Route Logic"
Cohesion: 0.09
Nodes (35): build_graph(), StateGraph, _fake_factory(), _no_extract(), _no_recall(), _no_suggest(), The whole point of the route. A client showing an empty chat wants chips, not…, An empty chat asking for openers must stay an empty chat. A message appended… (+27 more)

### Community 10 - "Evaluation Gate Logic"
Cohesion: 0.06
Nodes (31): _fake_duplicate_pair(), _fake_rule(), _hygiene_args(), A harness that can silently spend the month's budget is one that will., Stub every collaborator `_cmd_hygiene` calls so the test asserts only on the…, Report-only by default (docs/architecture.md): a bare `hygiene --member X` must…, --apply without EVE_EVAL_HYGIENE_APPLY_ENABLED must not supersede or prune -…, Ten points is above the noise floor of a nondeterministic replay. (+23 more)

### Community 11 - "OAuth Token Store"
Cohesion: 0.05
Nodes (30): Phase 2 writes personal memory into the store; scope every operation to the…, scope_store_to_member(), _install(), _spawn(), conn(), fixture, fixture, The token store. Provider-agnostic on purpose: a third wearable should be a new… (+22 more)

### Community 12 - "Memory Extraction Operations"
Cohesion: 0.07
Nodes (41): mark_replied(), A member speaking in an ambient thread IS the label (eval design 5). No lookup…, apply_operations(), extract(), _filter_authored(), _is_single_sentence(), load_extract_prompt(), _mark_replied_if_a_reply() (+33 more)

### Community 13 - "Calendar Signal Sources"
Cohesion: 0.10
Nodes (36): dataclasses, poll(), Calendar events as signals, via eve-tools' CalDAV client. Two signal shapes per…, True only when the event's start is beyond the lookahead - not yet imminent,…, _starts_later(), _starts_soon(), _budget_overruns(), _is_number() (+28 more)

### Community 14 - "Computer Settings Configuration"
Cohesion: 0.07
Nodes (38): Any, BaseSettings, Settings, _clear(), fixture, test_a_short_computer_api_key_is_rejected(), test_computer_is_disabled_by_default(), test_enabling_with_a_long_enough_key_is_accepted() (+30 more)

### Community 15 - "Git Repository Operations"
Cohesion: 0.12
Nodes (37): add_worktree(), _base_ref(), clone_path(), _clone_url(), ensure_clone(), GitError, publish(), Exception (+29 more)

### Community 16 - "Monarch Money Integration"
Cohesion: 0.08
Nodes (35): _current_month(), _fake_client(), _FakeMonarch, fixture, The query asks for `name`, so this should be unreachable against a real account…, Deriving the expected month through the same datetime.now() call the normalizer…, Enough of MonarchMoney to observe how it was authenticated., A Monarch account created through Google sign-in has no password, so a session… (+27 more)

### Community 17 - "Member Context Building"
Cohesion: 0.10
Nodes (36): build_member_context(), build_system_prompt(), datetime, _section(), MemberContext, TypedDict, _bundle(), _mem() (+28 more)

### Community 18 - "Message History Content"
Cohesion: 0.09
Nodes (34): HumanMessage, langgraph_config, langgraph_constants, Span, last_exchange(), AIMessage, What REFLEX and the digest summarizer are allowed to read as something a party…, The last thing the member said and the last thing Eve said, as plain strings.… (+26 more)

### Community 19 - "Coding Session Store"
Cohesion: 0.09
Nodes (33): psycopg_rows, psycopg_types_json, advance_cursor(), bump_supervisor_turns(), create_session(), get(), live_sessions(), live_sessions_for() (+25 more)

### Community 20 - "Agent Client Protocol"
Cohesion: 0.10
Nodes (27): acp, acp_schema, Client, PermissionOption, ReadTextFileResponse, RequestPermissionResponse, Any, Path (+19 more)

### Community 21 - "ACP Session Management"
Cohesion: 0.15
Nodes (25): create(), get(), kill(), send(), snapshot(), Turn, fake_spawn(), FakeConn (+17 more)

### Community 22 - "Coding Supervisor Decisions"
Cohesion: 0.15
Nodes (31): Decision, BaseModel, Returns the sessions that resolved - finished, failed, blocked, or stale - on…, tick(), _box(), fixture, The four-way decision, and what each outcome does to the row. The test that…, The subprocess and the worktrees stay up so the member's answer can resume this… (+23 more)

### Community 23 - "Ambient Pipeline Logic"
Cohesion: 0.10
Nodes (33): (fix wave item 1) A computer signal bypasses quiet hours exactly like an urgent…, (fix wave item 1) A member already at the daily cap still gets a computer…, `urgent` bypasses the cap and quiet hours, never the permission gate (fix round…, Design section 9: the trace only starts at the compose turn, so the verdict,…, The cooldown is both the most frequently taken path and, before this fix, the…, The case none of the fix-round-1 tests covered (fix round 2, item 1): a member…, The accepted trade-off from fix round 2, item 1: if a defer persists longer…, fix round 2, item 3: once the idempotence skip can fire for every member in the… (+25 more)

### Community 24 - "Evaluation CLI Tool"
Cohesion: 0.12
Nodes (28): random, check_ceiling(), _cmd_build(), _cmd_gate(), _cmd_run(), main(), `eve-eval`: build datasets, run them, gate on regressions, report hygiene. Runs…, _run_ambient() (+20 more)

### Community 25 - "Computer Task API"
Cohesion: 0.13
Nodes (30): delete, fastapi_responses, Queue, _check_auth(), close_session_route(), create_session_route(), create_task_route(), delete_session_route() (+22 more)

### Community 26 - "Database Migrations"
Cohesion: 0.07
Nodes (6): alembic, Alembic environment for Eve's own schema. Two things here are load-bearing and…, run_migrations_offline(), run_migrations_online(), _url(), sqlalchemy

### Community 27 - "Financial Specialist Testing"
Cohesion: 0.10
Nodes (23): GenericFakeChatModel, importlib, FakeToolCallingModel, `GenericFakeChatModel` raises `NotImplementedError` from `bind_tools` - fine…, test_denies_the_call_before_touching_the_model(), factory(), test_ask_finances_reads_transactions_through_eve_tools(), test_ask_home_calls_get_state_through_eve_tools() (+15 more)

### Community 28 - "Gmail OAuth Tooling"
Cohesion: 0.07
Nodes (21): importlib_util, parametrize, `scripts/gmail_oauth_setup.py` is operator tooling, not part of the package, so…, Re-running is the normal way to refresh a token, and it must not cost anyone…, The caller writes the returned object under a compare-and-set; the value it…, Better to make the operator look than to replace a blob we cannot read with one…, test_a_non_object_refuses_the_write(), test_an_empty_or_blank_value_is_treated_as_no_members() (+13 more)

### Community 29 - "Evaluation Scoring Metrics"
Cohesion: 0.10
Nodes (27): Judgement, _pct(), BaseModel, Fraction of assertions the judge marked satisfied, plus the canary. An item…, The number that justifies Phase 5a. Positive: self-authoring is working. Flat:…, Exact comparison against the recorded verdict, plus reply precision.…, rule_delta(), score_ambient() (+19 more)

### Community 30 - "Tools API Surface"
Cohesion: 0.10
Nodes (26): AsyncClient, base64, _check_auth(), healthz(), invoke_tool(), InvokeRequest, BaseModel, get (+18 more)

### Community 31 - "Coding Model Dispatch"
Cohesion: 0.12
Nodes (23): pydantic, available_models(), Which models a delegated coding session may use. NOT models.py, and…, The model to actually use. Falls back rather than raising: Eve has already told…, validate(), Eve's three coding tools. Permission is checked here, before the HTTP call, so…, _advance(), decide() (+15 more)

### Community 32 - "Tool Error Handling"
Cohesion: 0.10
Nodes (22): _handle_tool_error(), Exception, The plan's global constraint - every call to an external system degrades to a…, _enabled(), _noop(), fixture, THE test that matters most. _handle_tool_error degrades every tool exception to…, If you cannot approve, you cannot propose. There is no queue. (+14 more)

### Community 33 - "Calendar Source Testing"
Cohesion: 0.10
Nodes (29): _invoke_returning(), The horizon is wider than the lookahead precisely so a change to a far-off…, The filter reads only the one-line summary, so a `:rev:` summary asserting an…, The filter prompt renders `occurred_at` as "Occurred at"; a future start time…, A model should never be handed "…, starting None." (fix round 1 item E) -…, (fix round 4, item 2) If this source swallowed eve-tools' `error:` string into…, (rereview fix, item 2) `caldav_client.list_events` isolates a single failing…, (fix round 4, item 6) `_starts_soon` had no lower bound, so it answered true… (+21 more)

### Community 34 - "Finance Source Testing"
Cohesion: 0.11
Nodes (27): _fake_invoke(), _invoke(), The other direction: transactions fail, budgets succeed. Symmetric with the…, .replace(tzinfo=UTC) on an offset-bearing timestamp would silently shift the…, `{"transactions": 5}` is truthy, so `or []` never fires; the `for` statement…, `{"budgets": 5}` is truthy and non-iterable, so `or []` never fires and the…, The real Monarch payload nests merchant as {"name", "id", ...}, not a flat…, Nothing has happened. A signal per budget per poll would burn the filter's… (+19 more)

### Community 35 - "CalDAV Client Testing"
Cohesion: 0.10
Nodes (24): FakeEvent, _ics(), one_calendar(), fixture, The caldav library is synchronous and talks to a real server, so these tests…, `principal().calendars()` returns every collection the member owns - task…, A VEVENT that parses cleanly can still carry a property shape that breaks the…, (rereview fix, item 2) This test used to assert that a missing credential… (+16 more)

### Community 36 - "Ambient Notification Store"
Cohesion: 0.10
Nodes (27): already_notified(), decisions_since(), _execute(), _fetchone(), has_any(), is_fresh(), mark_seen(), notices_since() (+19 more)

### Community 37 - "Decision Retention Policy"
Cohesion: 0.07
Nodes (20): prune_decisions(), Retention. One row per judged signal at a five-minute poll across four sources…, pool(), fixture, Integration tests against the real Postgres in docker-compose.test.yml., (fix round 4, item 8) `prune_seen` used to delete any row past the horizon,…, This is what makes the first poll prime rather than notify., `(source, key)` is not unique over time - the same key legitimately re-fires… (+12 more)

### Community 38 - "Ambient Polling App"
Cohesion: 0.10
Nodes (24): collections, datetime, Request, _audience_for(), _handle_in_background(), healthz(), home_assistant_signal(), lifespan() (+16 more)

### Community 39 - "Graph Capability Testing"
Cohesion: 0.09
Nodes (17): _bound_types(), _declaring(), Pins the one behavior this task exists to guarantee: a thread checkpointed…, The seam exists for tests and eval. The DEFAULT must be the real node, or the…, The client already sends its catalog. Spending it only on a post-hoc refusal is…, Intersected with the server catalog, so a client advertising a type this server…, `capabilities()` only checks that `assistant_ui` is a dict. A non-list…, `search_skills` returns a Command (it updates `dynamic_tools`); a data tool… (+9 more)

### Community 40 - "UI Schema Projection"
Cohesion: 0.10
Nodes (25): langchain_core_utils_function_calling, _branches(), _component_items(), full(), fixture, `eve.ui.schema` is a PROJECTION of the server validator, not a sixth hand-…, The trip that actually matters: whatever langchain hands the model. An enum…, It rides in context on every turn a capable client is connected. The ceiling is… (+17 more)

### Community 41 - "Tool Authoring CLI"
Cohesion: 0.14
Nodes (25): approve_one(), main(), _run(), `eve-tool`: review, approve, reject and revoke Eve-authored tool code.…, Re-check the source at approval time. The propose-time check already ran, but…, _render(), revoke_one(), _status() (+17 more)

### Community 42 - "Computer Signal Testing"
Cohesion: 0.08
Nodes (17): _no_recently_resolved_by_default(), fixture, Every test in this file that doesn't care about the 24-hour re-derivation…, A task that resolved on this exact tick appears in both `sync()`'s return value…, Not "never recurs": a suppressed or deferred signal must get a real retry…, The scenario the fix wave describes: `poller.sync()` only returns a row on the…, test_a_task_in_both_sync_and_recently_resolved_is_not_duplicated(), test_a_task_missed_by_this_ticks_sync_is_still_recovered() (+9 more)

### Community 43 - "Personal Access Tokens"
Cohesion: 0.11
Nodes (23): clean_pats(), fixture, integration, Personal access tokens: minting, resolution, and revocation. The unit tier…, The whole reason this table exists rather than one shared secret., Otherwise a typo mints a token that 401s on first use with no clue why - the…, Revocation is by label, so two live tokens sharing one would make `revoke`…, Every OIDC request presents a JWT. If resolution did not short-circuit on the… (+15 more)

### Community 44 - "In-Memory Task State"
Cohesion: 0.10
Nodes (21): create(), get(), In-memory task state for the box. Not durable across a restart - eve-ambient's…, Task, clear(), drain(), _hold(), join() (+13 more)

### Community 45 - "Memory Rule Operations"
Cohesion: 0.12
Nodes (25): Operation, Catch a structured-model add that would turn one durable row into two facts., A kid cannot author a rule that changes how Eve treats the family., Catch a subject stored differently from lowercased search tokens., Profile and household are injected in full and never vector searched., `eve.suggest` imports this. A leading underscore across a module boundary is a…, test_a_rule_op_is_dropped_when_authoring_is_disabled(), test_a_rule_operation_is_written() (+17 more)

### Community 46 - "Ambient Filter Testing"
Cohesion: 0.11
Nodes (20): FakeStructuredModel, no_household_memory(), _load_always_on(), fixture, Final round, item 1: `langchain_core`'s `OutputParserException` (unknown tool…, Belt-and-braces (fix round 2, item 2, the related gap): if…, Without it the filter re-tells the family things they already know., Postgres being unreachable should cost the filter its context, not its ability… (+12 more)

### Community 47 - "Mail Source Testing"
Cohesion: 0.08
Nodes (20): int(x)/1000 on a huge string overflows datetime.fromtimestamp with…, gmail.py's hydration fills a missing header with "", present but falsy - not…, The exact defect this closes: a truthy non-list value passes `or []` unscathed…, `{"messages": 5}` is truthy, so `or []` never fires; the `for` statement itself…, The filter reads `summary` and nothing else, so the one line has to carry…, (fix round 4, item 2) eve-tools returns error strings rather than raising, and…, tools_client.invoke hands back the already-unwrapped result as JSON., A payload that legitimately owns a `result` key (unlikely here, but tool_result… (+12 more)

### Community 48 - "Langfuse Evaluation Replay"
Cohesion: 0.10
Nodes (17): _client(), publish_run(), Best-effort Langfuse upload. Langfuse is a publishing target, never a…, True on success. Never raises., One VOICE call per turn item per arm. Printed before a run starts so nobody…, Re-judge a recorded signal. A FilterError is reported, not raised: one…, replay_ambient(), _signal_from() (+9 more)

### Community 49 - "Tools Client Testing"
Cohesion: 0.14
Nodes (23): create_coding_session(), get_computer_task(), GET /tasks/{id} on eve-computer. `None` means the box could not be asked at all…, mock, A down eve-tools must not fail the whole turn - the caller is always a tool…, Malformed or non-JSON responses (proxy errors, truncated) must not raise…, test_a_dead_sandbox_returns_an_error_string(), test_close_returns_the_pull_requests() (+15 more)

### Community 50 - "Skill Authoring Logic"
Cohesion: 0.11
Nodes (16): _proc(), A procedure Eve wrote once and can never revise goes stale and stays stale. The…, The core invariant of this phase: a turn whose last human message carries the…, The guard must refuse the ambient turn without refusing every turn - a guard…, Global constraint: a tool returns an error string, never raises. A raise here…, test_parse_skill_text_falls_back_without_frontmatter(), test_write_skill_adds_a_procedure_row(), add() (+8 more)

### Community 51 - "End-to-End Integration Tests"
Cohesion: 0.12
Nodes (20): langgraph_sdk_errors, _client(), End-to-end integration tests against a live `aegra serve` instance. Requires…, A family member must not be able to delete another member's thread. Same…, Starting a run on the owner's own thread must not be denied by the…, Dispatch one turn and wait for the run to reach a terminal state. `runs.join`…, A dispatched turn must actually execute `load_context` under Aegra and reach…, The whole point of the credential: a scripted client connects with a minted PAT… (+12 more)

### Community 52 - "OAuth Token Refresher"
Cohesion: 0.14
Nodes (23): Refresher, get_pool(), AsyncConnectionPool, access_token(), configured_providers(), get_row(), _is_stale(), NotConnected (+15 more)

### Community 53 - "UI Wire Protocol"
Cohesion: 0.15
Nodes (23): append_frame(), _byte_length(), _compact(), frame(), The server side of the `assistant-ui/1.0` wire contract. A mirror of the…, Undo what `persist_ui` did to an AIMessage's content, for every place a…, `strip_frames` operates on a plain string; `AIMessage.content` is sometimes…, `None` when `operation` is a legal create/patch/delete, otherwise the same… (+15 more)

### Community 54 - "Whoop Client Testing"
Cohesion: 0.16
Nodes (21): mock, parametrize, WHOOP v2 client and normalizers. Every test fakes HTTP with respx and the token…, A UTC calendar date is not an acceptable substitute for provider time., _recovery_record(), test_a_401_refreshes_once_and_retries(), _refresh_now(), test_a_day_with_no_workouts_gets_an_empty_list_not_none() (+13 more)

### Community 55 - "Dynamic UI Testing"
Cohesion: 0.08
Nodes (19): fixture, `show_surface`: the model's entire share of the dynamic UI feature., The reported bug's SECOND failure. A model that fixed its invented type names…, `stream.emit` returns False outside a runnable context. Returning the artifact…, Properties are sorted, so the hint is stable across runs - a model retrying…, An unknown type is already rejected as `component-type`; the hint must not…, The catalog reaches the model through the ARGUMENT SCHEMA now…, The fix for the reported bug, stated as an invariant: a model that reads only… (+11 more)

### Community 56 - "AST Tool Inspection"
Cohesion: 0.15
Nodes (20): ast, Shapes only. No behaviour, no I/O, no internal imports., check(), _import_allowed(), The AST allowlist. NOT A SECURITY BOUNDARY. A determined bypass of an AST…, `json.decoder` is fine if `json` is allowed; `urllib.request` is not allowed by…, CheckResult, ToolProposal (+12 more)

### Community 57 - "Sandbox Tool Execution"
Cohesion: 0.18
Nodes (22): run_tool(), No environment variables cross the boundary. A tool that could read them could…, Critical 2 regression. A tool the AST checker fully accepts (no denied import,…, Important 3 regression. The runner writes its JSON result to the same stdout a…, Fix-wave re-review residual finding 1. A tool that writes more than…, The database and the caller disagree about approved bytes. A tampering signal,…, Fix-wave re-review residual finding 2. `_reap` used to close the stdout/stderr…, A wall clock and RLIMIT_CPU catch different failures: a sleep burns no CPU, a… (+14 more)

### Community 58 - "UI Action Recognition"
Cohesion: 0.16
Nodes (22): parse_action(), The decoded envelope, or None when `text` is ordinary member speech. Never…, _encoded(), Recognising a UI tap in what arrives as ordinary user text., Same id, so `add_messages` REPLACES rather than appends: the raw envelope would…, The client wraps unconditionally on every provider, LangGraph included -…, Tolerated so a future client that drops the markers on a native channel still…, Never guess. A half-arrived envelope has to become a normal Eve turn, not a… (+14 more)

### Community 59 - "Dynamic UI Surface"
Cohesion: 0.10
Nodes (22): capabilities(), emit(), RunnableConfig, The client's `DynamicUiCapabilities.toJson()`, or None. `config.configurable`,…, Whether the client declared EVERY id in `catalog_ids`. Fails CLOSED. A set…, Validate, then write one operation to the `custom` stream. `{"assistant_ui":…, supports(), build_create() (+14 more)

### Community 60 - "Health Provider Integration"
Cohesion: 0.13
Nodes (19): fixture, parametrize, The fan-out layer: which providers a member has, merging their answers,…, Spec 4: 1..14, enforced here, not by the caller. A model that asks for 900 days…, The one failure that must NOT degrade to an empty list. Broken auth reported as…, Both clients plus the store, with recorded calls., Spec 4: the specialist reports both rather than silently preferring one. Two…, stub() (+11 more)

### Community 61 - "Response Chip Generation"
Cohesion: 0.13
Nodes (23): _install(), The call succeeded and returned something unusable. Deterministic dead end - no…, The Flutter client reads `custom`, not state. Both exits come from one helper…, An empty list means 'clear the chips', which a client can only act on if it…, A direct call outside a runnable context - exactly what this test does - makes…, The tools loop gave up. That is not a conversation to offer continuations of., No human message means there is no member utterance to continue - the same…, Messages present, so the ambient/empty-human skip does not fire, but `member`… (+15 more)

### Community 62 - "Thread Title Generation"
Cohesion: 0.16
Nodes (10): _config(), FakeModel, _state(), test_generate_replaces_aegra_first_message_fallback(), test_generate_skips_titled_or_ambient_threads(), test_generate_swallows_model_and_update_failures(), test_generate_writes_a_reflex_title(), get_thread() (+2 more)

### Community 63 - "Context Loading Node"
Cohesion: 0.13
Nodes (20): collections_abc, functools, re, load_context(), load_persona(), RunnableConfig, The `load_context` node. Performs NO model call. Everything here is local…, _aegra_default_title() (+12 more)

### Community 64 - "Memory Integration Tests"
Cohesion: 0.15
Nodes (21): requires_litellm_key, clean_memory(), fixture, End-to-end memory through a live `aegra serve`. The unit tests prove each part…, An empty day-one store must produce a complete turn, not an exception., DoD item 2. `extract` decides *when* to supersede via the REFLEX model…, DoD item 3. As with supersession, the natural-language "forget that" trigger…, DoD items 4 and 5, against the real deployed LiteLLM proxy. Gemini was… (+13 more)

### Community 65 - "Family Roster Management"
Cohesion: 0.12
Nodes (13): Family, Path, fixture, The poll loop walks the roster per member; without this it would have to reach…, A guard, not a roster snapshot: if this grant were ever dropped from…, roster(), test_a_member_without_a_wardrobe_album_gets_none(), test_a_wardrobe_album_is_read_from_the_roster() (+5 more)

### Community 66 - "Skill Serialization Logic"
Cohesion: 0.11
Nodes (15): SKILL.md's on-disk shape, so parse_skill_text round-trips it. Built with…, serialize_procedure(), A colon-space in the description ('...sitter: call Sam first.') is exactly the…, test_serialize_round_trips_a_description_containing_a_colon(), test_serialize_round_trips_through_the_shared_parser(), Mirrors test_load_always_on_omits_rules_by_default for the procedure layer:…, Fail closed: the kill switch must hold even against a spec the DB still lists…, test_load_skills_parses_an_authored_row_like_a_file() (+7 more)

### Community 67 - "UI Persistence Logic"
Cohesion: 0.13
Nodes (21): _is_operation(), persist_ui(), AIMessage, `{}` for every turn that emitted no surface - which is nearly all of them., A dynamically-materialized tool may set an artifact for its own reasons; only a…, `model_copy`, not a freshly-built `AIMessage`: `add_messages` replaces by id,…, _with_frame(), _create() (+13 more)

### Community 68 - "Memory Task Concurrency"
Cohesion: 0.09
Nodes (12): _clean_registry(), fixture, The budget bounds the WAIT, not the work. A slow extraction must still land -…, Two runs can overlap on one thread - Aegra does not prevent it. The older task…, Regression for the bug where `join` only ever looked up the newest task per…, test_a_failing_task_does_not_raise_into_join(), test_a_second_spawn_does_not_strand_the_first(), test_an_anonymous_task_is_still_awaited_by_drain() (+4 more)

### Community 69 - "Memory Recall Testing"
Cohesion: 0.13
Nodes (15): A resumed run can reach recall with no new human turn. Embedding an empty…, The previous turn's writes must be visible to this turn's reads, or 'what did I…, A degraded turn is a complete turn. If the previous extraction is wedged, this…, The load-bearing property of the whole design. An untested degrade path does…, _state(), test_a_failing_embedding_degrades_rather_than_raising(), test_a_slow_embedding_degrades_to_lexical_rather_than_failing(), test_a_stalled_extraction_does_not_hang_the_turn() (+7 more)

### Community 70 - "UI Stream Handshake"
Cohesion: 0.13
Nodes (19): _config(), _create(), The capability handshake in, and `custom`-mode frames out., Per-type gating rather than a version check is what keeps a phone on an old…, A tree of zero components is degenerate but not a capability failure -…, LangGraph indexes run metadata and rejects a non-scalar value there, so the…, A client that declared nothing cannot render anything. Emitting at it would put…, `get_stream_writer()` raises outside a runnable context. Every caller is a tool… (+11 more)

### Community 71 - "Database Migration CLI"
Cohesion: 0.14
Nodes (19): argparse, psycopg_pool, _run(), close_pool(), main(), _run(), migrate(), Connection pool and schema migration for Eve's own tables. Migrations run… (+11 more)

### Community 72 - "Authentication Error Handling"
Cohesion: 0.14
Nodes (19): authenticate(), AuthError, extract_bearer(), Raised for any failure to authenticate. Subclasses the SDK's HTTPException so…, Pull the bearer token out of headers whose keys/values may be bytes., An empty configured token must never match an empty presented one., `compare_digest` raises TypeError on a `str` operand containing non-ASCII. A…, Same defect, arriving through the bytes-header path the way Aegra actually… (+11 more)

### Community 73 - "Whoop Data Normalization"
Cohesion: 0.23
Nodes (20): _cycle_dates(), _get(), get_activity(), get_recovery(), get_sleep(), _hours(), _newest_first(), _num() (+12 more)

### Community 74 - "Specialist Agent Testing"
Cohesion: 0.14
Nodes (16): _AgentStub, _factory_with(), get_widget(), tool, The ChatGPT backend rejects plain system messages outright - live- verified…, A model that emits `rounds` tool calls before answering., EVE-15: `specialist_max_iterations` is a count of model+tool rounds, but…, The outer loop has `_LOOP_EXHAUSTED` for this; the inner loop leaked `error:… (+8 more)

### Community 75 - "Notification Delivery Pipeline"
Cohesion: 0.15
Nodes (18): Protocol, _click_url(), _client(), compose_prompt(), deliver(), _discard(), _final_text(), _is_veto() (+10 more)

### Community 76 - "Ambient Audience Filtering"
Cohesion: 0.15
Nodes (16): Private correspondence is not the filter's to redistribute, and no permission…, A family calendar is shared logistics: a kid's game on one calendar is news for…, The filter names subs; a hallucinated one must not kill the tick., Fail closed: a source added without a permission mapping notifies no one…, The filter's audience is an unconstrained model-produced list; a repeated sub…, _signal(), test_a_calendar_signal_may_notify_someone_else(), test_a_duplicated_sub_is_deduped_preserving_order() (+8 more)

### Community 77 - "Live Ambient Integration"
Cohesion: 0.11
Nodes (9): pytest, skipif, One fabricated Home Assistant signal, all the way through: a real REFLEX…, Urgent by design: it is the one shape whose verdict is predictable enough to…, The other half of the contract: the filter has to say no to something plainly…, test_a_non_event_is_filtered_by_the_real_reflex_model(), test_a_water_leak_reaches_a_real_notification(), Statement-level tests: the store's job is to emit the right SQL with the right… (+1 more)

### Community 78 - "Memory Extraction Prompting"
Cohesion: 0.12
Nodes (11): Extraction, BaseModel, Digest setup runs after streaming and must not be able to fail the turn., Currently incidental - last_exchange reads only Human and AI messages. This…, `persist_ui` (`eve.ui.persist`) writes this turn's `<assistant-ui>` frame into…, test_the_extraction_prompt_strips_a_persisted_frame(), ainvoke(), test_tool_messages_never_reach_the_extraction_prompt() (+3 more)

### Community 79 - "Skills Registry Loader"
Cohesion: 0.16
Nodes (17): _load_skill_md(), load_skills(), parse_skill_text(), Loads the skills index: SKILL.md procedures from disk, MCP tool descriptions…, Split a SKILL.md-shaped document into (name, description, body, specialist).…, The skills corpus: SKILL.md files on disk, MCP tool metadata, and Eve-authored…, An empty corpus must never be SILENT. `skills_dir` defaults to the relative…, A directory that exists but ships no SKILL.md is the same failure wearing a… (+9 more)

### Community 80 - "Pipeline Eval Stubs"
Cohesion: 0.12
Nodes (14): pipeline_stubs(), _judge(), fixture, Two members, one delivery raises: the whole signal is deferred, not just the…, Replace the three I/O seams — store, filter, notify — and keep the real gates,…, The other half of item 1: a retry must not re-deliver, re-push, or re-spend the…, A self-contained set of stubs for the eval-recording tests below: freshness,…, test_a_partial_defer_leaves_the_signal_unseen() (+6 more)

### Community 81 - "Memory Erasure Guards"
Cohesion: 0.12
Nodes (13): now(), Same guard, the other erasure path: supersede replaces a rule's content just as…, The rule-erasure guard must not spill over onto ordinary fact maintenance.…, test_a_forget_targeting_a_non_rule_fact_still_works_on_an_ambient_turn(), ainvoke(), overlapping(), overlapping(), test_a_supersede_targeting_a_rule_is_refused_on_an_ambient_turn() (+5 more)

### Community 83 - "MCP Tool Registry"
Cohesion: 0.16
Nodes (15): Command, InjectedToolCallId, Static metadata for registered MCP servers - name, description, and argument…, register(), registered_mcp_tools(), InjectedState, tool, Search for a known procedure or a newly-available tool matching a request… (+7 more)

### Community 84 - "JWT Auth Testing"
Cohesion: 0.16
Nodes (17): cryptography_hazmat_primitives, cryptography_hazmat_primitives_asymmetric, _b64url(), oidc(), fixture, EVE_AMBIENT_TOKEN with a trailing newline is the classic .env copy-paste. The…, The ambient credential is not gated on auth_mode: production runs oidc and this…, test_a_jwt_bearer_does_not_cost_a_pat_lookup() (+9 more)

### Community 85 - "Immich Tool Testing"
Cohesion: 0.14
Nodes (13): httpx, fixture, _settings(), test_album_assets_is_capped_and_marked_truncated(), test_album_assets_returns_ids_and_filenames(), handler(), test_asset_image_returns_base64_and_content_type(), _transport() (+5 more)

### Community 86 - "Shared Test Fixtures"
Cohesion: 0.14
Nodes (14): langchain_core_language_models_fake_chat_models, aegra_server(), _clear_caches(), eve_sandbox_server(), eve_tools_server(), fixture, Shared test fixtures. `get_settings`, `get_model`, `get_family`, `load_persona`…, A stand-in for the real home lab's Home Assistant instance, run in-process on a… (+6 more)

### Community 87 - "Ntfy Notification Provider"
Cohesion: 0.22
Nodes (17): NtfyNotifier, ntfy_settings(), fixture, mock, ntfy carries metadata in headers, which are latin-1 on the wire. Eve's body may…, click_url is built from EVE_AMBIENT_THREAD_URL_TEMPLATE, a configuration value…, ntfy being down must lose the push, not the turn that produced it., test_a_failing_push_returns_false_rather_than_raising() (+9 more)

### Community 88 - "Computer Task Store"
Cohesion: 0.17
Nodes (16): create_task(), mark_finished(), Every task Eve is still waiting on. The poller (Task 5) asks the box about each…, `status` is `'finished'` or `'failed'` - the poller decides which by inspecting…, running_tasks(), _finish_at(), pool(), datetime (+8 more)

### Community 89 - "Memory Conflict Resolution"
Cohesion: 0.16
Nodes (15): Contradictions, _cosine(), find_duplicates(), BaseModel, Cosine similarity. A bare dot product is correct ONLY if both vectors are unit-…, (keeper, loser, score) for each near-identical pair in one scope. Auto-…, Resolving a conflict means choosing what the family wants., _rule() (+7 more)

### Community 90 - "Home Assistant Tools"
Cohesion: 0.13
Nodes (18): get_budgets(), list_transactions(), tool, List recent transactions, optionally filtered by category., Read current budget and cash-flow summary., call_service(), get_state(), list_entities() (+10 more)

### Community 91 - "Ambient State Markers"
Cohesion: 0.13
Nodes (17): is_ambient_text(), _last_write_wins(), True when this message was composed by the ambient pipeline rather than typed…, Last-write-wins, shared by `dynamic_tools` and `suggestions`. A reducer is what…, Chips describe ONE turn. Appending would accumulate the whole conversation's…, `suggest` must recognise this reply to skip chips for it, and `graph.py`…, One owner for the string. If the guard and the prefix ever decouple, an ambient…, test_ambient_marker_round_trips() (+9 more)

### Community 92 - "PAT Authentication Logic"
Cohesion: 0.14
Nodes (17): _ambient_settings(), _pat_settings(), The header is only meaningful alongside the ambient token. If an ordinary…, Aegra hands headers through as bytes in some paths; extract_bearer already…, The on-behalf-of header belongs to the ambient credential alone. A PAT is one…, Removing someone from family.yaml revokes their tokens implicitly., The prefix is what routes a bearer to the PAT table. An opaque dev token…, test_a_dev_token_is_still_accepted_alongside_the_pat_path() (+9 more)

### Community 93 - "Oura Client Testing"
Cohesion: 0.20
Nodes (15): mock, Oura v2 client and normalizers. The join in `get_recovery` is the thing to keep…, daily_sleep carries the score; the `sleep` collection carries the durations.…, _sleep_record(), test_a_401_refreshes_once_and_retries(), test_a_daily_score_with_no_detailed_row_nulls_the_durations(), test_a_missing_step_count_is_none_not_zero(), test_a_nap_does_not_win_the_join_over_the_nights_sleep() (+7 more)

### Community 94 - "Gmail API Client"
Cohesion: 0.17
Nodes (16): Credentials, email_mime_text, google_auth_transport_requests, google_oauth2_credentials, googleapiclient_discovery, _credentials_for(), _flatten(), get_thread() (+8 more)

### Community 95 - "Resource Scoping Auth"
Cohesion: 0.14
Nodes (16): hmac, jwt, PyJWKClient, read, allow_assistant_read(), _ambient_subject(), _header(), _jwk_client() (+8 more)

### Community 96 - "Sandbox HTTP API"
Cohesion: 0.17
Nodes (15): pydantic_settings, _check_auth(), _gate(), healthz(), invoke(), InvokeBody, BaseModel, get (+7 more)

### Community 97 - "PAT Management CLI"
Cohesion: 0.17
Nodes (16): active(), generate(), hash_token(), looks_like_pat(), main(), _run(), mint(), Personal access tokens: one long-lived, individually revocable credential per… (+8 more)

### Community 98 - "Oura API Client"
Cohesion: 0.26
Nodes (16): _get(), get_activity(), get_recovery(), get_sleep(), _hours(), _newest_first(), _num(), Oura v2 client. Plain httpx, documented REST. Two things differ from WHOOP and… (+8 more)

### Community 99 - "Wardrobe Catalog Sync"
Cohesion: 0.17
Nodes (16): _album_asset_list(), album_for(), The sync, and the one text rendering of a wardrobe. `sync` is a batch job. It…, The sync body. `limit` bounds how many photographs one call will describe, so…, `(note, uncatalogued)`. One extra API call, no vision, no measurable latency -…, The whole catalogue as one string, grouped by category., The member's Immich album id, from the roster. `None` when they have no…, `(assets, truncated, error)`. `invoke` returns a JSON string, or a string… (+8 more)

### Community 100 - "Coding Dispatch Tests"
Cohesion: 0.21
Nodes (16): _config(), fixture, Mirrors tests/test_computer_dispatch.py. The load-bearing test in this file is…, _stubs(), test_a_box_failure_leaves_no_orphan_row(), test_a_member_cannot_interject_into_another_members_session(), test_a_member_without_the_permission_never_reaches_the_box(), test_an_agent_eve_names_is_honoured() (+8 more)

### Community 101 - "Docker Image Integration"
Cohesion: 0.13
Nodes (13): computer_container(), computer_image(), _docker_available(), fixture, Builds the real image from Dockerfile.eve-computer, runs a container, and…, EVE-4 adds three ACP agents and `gh`. A missing one fails at the first session,…, A wiped PVC must recover model routing with no human involved (design doc:…, Smoke-level coverage for the one piece nothing else can test without a real X… (+5 more)

### Community 102 - "Vector Embedding Tests"
Cohesion: 0.13
Nodes (9): _fake(), FakeEmbedder, fixture, MRL truncation breaks unit norm. Cosine distance over non-normalised vectors…, Extraction frequently produces no new rows. A round trip to Gemini to embed…, Returns 3072 non-normalised dims, which is what gemini-embedding-001 emits…, test_a_zero_vector_raises_rather_than_dividing_by_zero(), test_empty_input_does_not_call_the_api() (+1 more)

### Community 103 - "Suggestion Node Tests"
Cohesion: 0.12
Nodes (10): Unit tests for the suggestion node. One test per outcome, so a regression names…, Fails CLOSED to a normal turn. This flag stops Eve answering at all, so a stray…, A chip is rendered verbatim in a pill. A paragraph breaks the UI, and…, The prompt asks for 2-4. There is deliberately no floor: discarding a usable…, `with_structured_output` is contracted to return a `Suggestions`, but a…, test_clean_drops_overlong_entries(), test_clean_keeps_a_single_suggestion(), test_clean_survives_a_model_returning_the_wrong_type() (+2 more)

### Community 104 - "CalDAV Calendar Client"
Cohesion: 0.16
Nodes (13): asyncio, caldav, icalendar, _as_utc_iso(), _calendars(), _credentials_for(), list_events(), _run() (+5 more)

### Community 105 - "OAuth Provisioning Server"
Cohesion: 0.17
Nodes (13): http_server, authorize_url(), _await_code(), _exchange(), expires_at(), _main(), datetime, One-time provisioning of a member's WHOOP or Oura credential. uv run python -m… (+5 more)

### Community 106 - "Path Confinement Security"
Cohesion: 0.20
Nodes (15): PathEscapedRoot, Exception, An `fs/*` path resolved outside the session root., _client(), parametrize, Path, The box's side of the protocol. Confinement is the load-bearing part: the agent…, test_a_symlink_pointing_out_of_the_root_is_refused() (+7 more)

### Community 107 - "Signal Filter Pipeline"
Cohesion: 0.12
Nodes (13): _handle(), Builds one Signal and drives it through `handle_signal`, tuning only the knobs…, One row per judged signal, carrying the whole Signal so the dataset item is…, The dataset measures the filter, not the gates. If the cap rewrote the label,…, `stale` resolves before the filter runs: there is no decision., A FilterError is a couldn't-decide. Recording it as a decision would put an un-…, Best-effort, like every other non-essential write here: losing an eval row must…, test_a_capped_signal_still_records_notify_true() (+5 more)

### Community 108 - "Model Catalogue Tests"
Cohesion: 0.19
Nodes (15): fixture, mock, The deny-list is one prefix and it is not a survey. ADR 0004 probed ocp/* live:…, A proxy outage must not make delegation impossible - the agent's own default is…, A bad name would otherwise kill the session at its first prompt, several…, _reset(), test_an_unreachable_proxy_yields_an_empty_catalogue_not_an_exception(), test_ocp_models_are_denied() (+7 more)

### Community 109 - "Tool Dispatch Routing"
Cohesion: 0.20
Nodes (14): _api_key(), _client(), fixture, The dispatch table is the whole routing layer - a handler that exists but is…, test_health_get_recovery_defaults_days_to_one(), test_health_get_recovery_dispatches_with_member_and_days(), test_healthz_needs_no_auth(), test_invoke_dispatches_immich_album_assets() (+6 more)

### Community 110 - "UI Schema Validation"
Cohesion: 0.12
Nodes (16): A `$`-prefixed string that is not a legal binding is a `binding` error, not a…, The provider guide's `$data.forecast.0.label` example contradicts the client…, _surface(), test_a_catalog_id_outside_the_closed_v1_set_is_rejected(), test_a_catalog_version_other_than_1_is_rejected(), test_a_component_type_outside_the_closed_v1_set_is_rejected(), test_a_definition_over_forty_eight_kibibytes_is_rejected(), test_a_malformed_data_binding_is_rejected_as_a_binding_error() (+8 more)

### Community 111 - "Claude Computer Use"
Cohesion: 0.17
Nodes (13): claude_agent_sdk, ClaudeAgentOptions, pathlib, ResultMessage, computer(), tool, Anthropic's reference computer-use tool, lifted rather than rewritten (design…, _screenshot() (+5 more)

### Community 112 - "Gmail OAuth Setup"
Cohesion: 0.19
Nodes (14): google_auth_oauthlib_flow, client_config(), main(), merge_member(), CompletedProcess, Run locally, once per family member, to obtain a Gmail OAuth refresh token and…, This member's entry, added to whatever is already there. Deliberately a read-…, Store the merged object, failing rather than clobbering a concurrent write. The… (+6 more)

### Community 113 - "UI Action Protocol"
Cohesion: 0.15
Nodes (14): json, _clean(), The inbound half of the protocol. A tap on a rendered surface is not a separate…, Turn a Save tap into an ordinary turn, then get out of the way. No model call…, What the raw envelope is replaced with in the transcript. A reopened session…, readable_submission(), ui_submit(), A form with no inputs, or every field left blank. The model needs a turn it can… (+6 more)

### Community 114 - "Ambient Integration Tests"
Cohesion: 0.19
Nodes (11): _ambient_client(), _member_client(), Ambient's impersonation path against a live `aegra serve`. Requires `docker…, This is the whole point of impersonating rather than pushing only: the member…, Belt and braces on the unit test in Task 9: at the HTTP boundary, the header…, The veto path deletes the thread it just created; it must be allowed to., test_a_member_token_with_the_header_still_authenticates_as_itself(), test_another_member_cannot_read_it() (+3 more)

### Community 115 - "Agent Registry Management"
Cohesion: 0.20
Nodes (12): build(), Exception, Agent name + model in, argv + environment out. Three entries in a dict. No…, An agent name outside AGENT_NAMES., UnknownAgent, fixture, _settings(), test_an_unknown_agent_is_refused() (+4 more)

### Community 116 - "Ambient Turn Guards"
Cohesion: 0.13
Nodes (10): ambient_marker(), The guard that matters. Ambient content is untrusted input: a phishing email…, The guard is scoped to authoring. Phase 4 ships fact extraction on ambient…, The guard covers erasure, not just authoring. An ambient turn cannot write a…, test_a_forget_targeting_a_rule_is_refused_on_an_ambient_turn(), ainvoke(), test_a_rule_op_is_refused_on_an_ambient_turn(), test_facts_are_still_extracted_on_an_ambient_turn() (+2 more)

### Community 117 - "Tool Approval Store"
Cohesion: 0.18
Nodes (14): live_tools(), Approved and not revoked. Read on every search_skills call., reject(), pool(), fixture, The partial unique index IS the approval invariant in the schema., test_approve_then_live(), test_cli_approve_and_revoke_round_trip() (+6 more)

### Community 118 - "Memory Graph Execution"
Cohesion: 0.14
Nodes (10): Recall is what makes an opener reflect who is asking. Extract has no exchange…, Recall must inform the answer it precedes; extract must not delay it., test_an_openers_request_still_runs_recall_but_not_extract_or_suggest(), suggest(), test_memory_reaches_the_system_prompt(), test_the_graph_runs_recall_before_eve_and_extract_after(), extract(), recall() (+2 more)

### Community 119 - "Member Data Isolation"
Cohesion: 0.13
Nodes (15): _insert(), The isolation that matters most in this whole phase. A profile fact is the most…, Entity matching is the arm that carries names, and names are most of family…, Rows are written before they are embedded, and the embedding call can fail. A…, `load_always_on` already proves member isolation for profile facts; the…, The other half of the scope predicate: a household-scoped episode is not owned…, test_always_on_does_not_leak_another_members_profile(), test_always_on_returns_profile_household_and_digest() (+7 more)

### Community 120 - "Empty Thread Openers"
Cohesion: 0.13
Nodes (15): _empty_state(), A brand new thread: no messages at all, which is exactly what the client's…, Who is asking and when are the only two signals an empty chat has. An opener…, A REFLEX model reading `Noah:` followed by nothing fills the blank in, which…, One switch for both chip flavours: a deployment that turned chips off must not…, An empty canvas waiting on a slow REFLEX call is a blank screen, so the budget…, The empty canvas shows nothing at all when this fails, which is the designed…, The client has ONE `suggestions` handler. Openers arriving under a different… (+7 more)

### Community 121 - "UI Component State"
Cohesion: 0.13
Nodes (15): _create(), `stateKey` names a localState slot to WRITE. A `$data.` binding resolves…, Both would mean one tap with two meanings, and the client would have to pick an…, A button that does nothing renders as a live control that silently ignores taps…, test_a_button_may_not_do_both(), test_a_button_may_not_do_neither(), test_a_button_may_set_local_state(), test_a_button_may_submit() (+7 more)

### Community 122 - "Home Assistant Integration"
Cohesion: 0.22
Nodes (13): respx, fixture, mock, HA sends every attribute of every entity. Forwarding that wholesale would spend…, _settings(), test_call_service_posts_to_home_assistant(), test_get_state_reads_from_home_assistant(), test_get_state_sends_the_bearer_token() (+5 more)

### Community 123 - "Vector Database Queries"
Cohesion: 0.18
Nodes (14): pgvector's text input format. ponytail: a string literal cast with `%s::vector`…, to_pgvector(), Live, non-superseded rules visible to one member, paired with their embedding -…, rules_with_embeddings(), Same isolation proof as the lexical arm, for the nearest-neighbour path: a…, `eve.eval.hygiene.find_duplicates` needs real floats, not pgvector's text…, A rule is written before it is embedded, and the embed call can fail - the same…, test_rules_with_embeddings_excludes_another_members_rule() (+6 more)

### Community 124 - "Health Provider Fan-out"
Cohesion: 0.19
Nodes (10): _clamp_days(), _fan_out(), get_activity(), get_recovery(), get_sleep(), Fan-out across whichever health providers a member has connected. Knows both…, 1..MAX_DAYS. Clamped here rather than trusted: `days` arrives from a model, and…, skipif (+2 more)

### Community 125 - "Vision Analysis Model"
Cohesion: 0.20
Nodes (13): _coerce_category(), describe(), _prompt(), BaseModel, One REFLEX-tier structured-output call per photograph. REFLEX rather than…, One garment. Field descriptions are the model's instructions, so they are…, Takes a wrapping object, not a bare list, for the reason `suggest.Suggestions`…, The model is told the six categories and mostly obeys. `accessory` is the safe… (+5 more)

### Community 126 - "Live Computer Environment"
Cohesion: 0.20
Nodes (13): _exec(), CompletedProcess, fixture, parametrize, Checks that are only meaningful against the deployed pod - the design doc's…, The egress policy is deny-by-default for RFC1918/cluster ranges, not deny-…, DoD 1: the PVC-backed home, not just the pod, is what must persist., _require_live() (+5 more)

### Community 127 - "Recall Token Budgets"
Cohesion: 0.16
Nodes (10): _budget_rows(), parametrize, Disabled must still produce a well-formed bundle: build_system_prompt and every…, Five equal 100-token rows, so a 1200-token budget split three ways (400) keeps…, EVE_SELF_AUTHORING_ENABLED=false must behave EXACTLY like Phase 4. An…, test_recall_bundle_always_has_a_rules_key(), test_recall_loads_rules_when_authoring_is_enabled(), load_always_on() (+2 more)

### Community 128 - "Memory Search Integration"
Cohesion: 0.23
Nodes (9): _memory(), _state(), test_search_memory_degrades_to_lexical_only_on_embedding_failure(), _lexical(), test_search_memory_merges_lexical_and_vector_results(), _embed(), _lexical(), _vector() (+1 more)

### Community 129 - "Health Specialist Tests"
Cohesion: 0.15
Nodes (11): _model_with(), Spec 5.1. A wearable-derived LLM opinion on a symptom reads as authoritative…, Spec 4.1 is a contract the model has to honour too - it is the thing that turns…, Spec 4.3.1: an empty recovery result before wake-up is normal, and a coach that…, The eve-tools handler table keys are health.get_recovery / get_sleep /…, test_a_member_without_the_health_permission_is_denied(), test_all_three_tools_exist_with_the_names_eve_tools_dispatches(), test_ask_health_reads_recovery_through_eve_tools() (+3 more)

### Community 130 - "Monarch Money Client"
Cohesion: 0.26
Nodes (12): gql, MonarchMoney, _authenticate_with_token(), _authenticated(), _client(), get_budgets(), _is_number(), list_transactions() (+4 more)

### Community 131 - "Ambient Dataset Evaluation"
Cohesion: 0.17
Nodes (12): ambient_items_from_rows(), Shape a decision row into an item, skipping anything unreplayable. A signal…, A run where the canary passes means the judge is rubber-stamping. This is the…, replay_turn calls the real graph, which resolves `member` through…, A malformed jsonb blob must be skipped, not crash the build., The harness imports Eve; Eve never imports the harness. Otherwise a bug in the…, test_build_ambient_excludes_a_row_whose_signal_will_not_rehydrate(), test_build_ambient_shapes_a_decision_row() (+4 more)

### Community 132 - "Specialist Skill Scoping"
Cohesion: 0.24
Nodes (10): _raise_runtime_error(), Tests for a specialist's scoped skills search., A specialist's loop is create_agent's own message state, not EveState. There is…, Same contract as every specialist tool: a filesystem failure returns an error…, _skills_dir(), test_a_failing_corpus_returns_an_error_string(), test_a_specialist_sees_only_its_own_skills(), test_a_specialist_with_no_skills_gets_a_clean_answer() (+2 more)

### Community 133 - "Coding Lifecycle Integration"
Cohesion: 0.24
Nodes (12): box(), origin(), fixture, The full session lifecycle against the real HTTP surface, the real ACP…, eve-computer, wired to a stub agent and a local origin., Create, turn, idle, reply, close - ending in a real branch with real commits…, _run(), test_a_session_runs_a_full_lifecycle() (+4 more)

### Community 134 - "Gmail API Integration"
Cohesion: 0.22
Nodes (11): _full_message(), fixture, A non-dict stub read in the except handler's own `stub.get("id")` log line…, The metadata-format body `messages().get()` returns: headers as a list of…, Gmail's header names are case-insensitive on the wire; a client that sends them…, _settings(), test_a_failed_metadata_fetch_skips_only_that_message(), test_a_missing_header_falls_back_rather_than_raising() (+3 more)

### Community 135 - "Turn Extraction Stamping"
Cohesion: 0.18
Nodes (8): Drive the real extract node with a fake REFLEX model returning `ops`., Eve's own opening message is not a reply to herself., _run_extract(), ainvoke(), test_a_member_turn_in_an_ambient_thread_stamps_replied_at(), mark_replied(), test_a_stamp_failure_does_not_fail_the_turn(), test_an_ambient_turn_does_not_stamp_replied_at()

### Community 136 - "Embedding Budget Management"
Cohesion: 0.18
Nodes (11): _clean_pending_registry(), _mem(), fixture, Catch a deadline applied only after slow store reads complete., Several tests below (real-join and stalled-extraction cases) call the actual…, test_embedding_that_finishes_after_its_budget_is_not_used(), slow_always_on(), wired() (+3 more)

### Community 137 - "Skill Revision Integration"
Cohesion: 0.18
Nodes (10): clean_pool(), fixture, integration, DoD 2: write_skill in one thread, search_skills finds it in another., DoD 3: the superseded_by chain records the revision., DoD 1, 4 and 8: authored rule -> next turn's prompt -> revoked -> gone, with…, test_a_rule_reaches_the_next_turns_prompt_then_is_revoked(), test_an_authored_procedure_is_retrievable_in_another_thread() (+2 more)

### Community 138 - "Wardrobe Specialist Tests"
Cohesion: 0.15
Nodes (3): The binding contract: a tool returns a string and never raises - a database…, test_read_wardrobe_degrades_when_the_store_raises(), test_the_stylist_reads_the_wardrobe_through_its_loop()

### Community 139 - "Sandbox Security Integration"
Cohesion: 0.15
Nodes (12): pool(), fixture, DoD 7's second half, and the claim §6.3 rests on: with the AST checker bypassed…, DoD 12. The sandbox is the one package that must be unable to reach anything:…, DoD 1, 3: the whole path, with the interrupt resolved by the CLI., DoD 4: the old version keeps serving until the new one is approved., DoD 7's first half: the checker runs again at approval time, so a row edited…, test_a_changed_source_needs_a_fresh_approval() (+4 more)

### Community 140 - "UI Protocol Streaming"
Cohesion: 0.17
Nodes (4): Reasoning-capable models return `content` as a list of typed blocks.…, The server side of `assistant-ui/1.0`, tested against the shapes the client's…, test_append_frame_appends_a_new_block_to_list_content(), test_strip_frames_from_content_drops_the_whole_block_for_list_content()

### Community 141 - "MCP Tool Dispatcher"
Cohesion: 0.17
Nodes (7): langgraph_sdk, os, A manual REPL for talking to Eve, streaming her reply token by token. Points at…, sys, mock_server_script(), fixture, Exercises the generic MCP dispatcher against a real local MCP server run over…

### Community 142 - "Wardrobe Asset Store"
Cohesion: 0.17
Nodes (3): psycopg_errors, pool(), fixture

### Community 143 - "Subprocess Execution Wrapper"
Cohesion: 0.18
Nodes (11): signal, _kill_group(), _package_root(), One subprocess per call. No reuse, no warm pool. No pool because process…, Read from `stream` until EOF or until more than `cap` bytes have arrived,…, Like `_read_capped`, but never stops reading once the cap is crossed: it keeps…, The directory containing the eve_sandbox package, so the child (run with -P -s,…, start_new_session puts the child in its own process group, so a tool that… (+3 more)

### Community 144 - "Notification Quiet Hours"
Cohesion: 0.29
Nodes (11): day_start_utc(), in_quiet_hours(), local_now(), parse_window(), datetime, The gates between a signal and an interruption. Pure functions only: no I/O, no…, Midnight of the member's current local day, expressed in UTC. This is the lower…, An owner-only signal notifies its owner and nobody else — including when the… (+3 more)

### Community 145 - "Signal Delivery Pipeline"
Cohesion: 0.24
Nodes (11): permitted(), DeliveryError, Exception, Infrastructure failed rather than Eve declining. The caller must leave the…, handle_signal(), datetime, One signal, from arrival to resolution. The only module that knows the order…, One line per signal, whatever happened to it (design section 9). The Langfuse… (+3 more)

### Community 146 - "Computer Task Polling"
Cohesion: 0.30
Nodes (10): poll(), Finished computer tasks as signals. The relevance filter is bypassed for this…, _summary(), src_eve_computer_init, _config(), _state(), test_a_dispatch_failure_is_returned_and_nothing_is_recorded(), test_a_member_without_the_permission_is_denied() (+2 more)

### Community 147 - "Ambient Coding Sessions"
Cohesion: 0.27
Nodes (11): fixture, Mirrors tests/test_ambient_sources_computer.py, including the 24-hour re-…, _session(), _stubs(), test_a_blocked_session_carries_its_question(), test_a_failed_session_reports_the_error(), test_a_finished_session_names_its_pull_requests(), test_a_session_with_no_commits_says_so_rather_than_claiming_success() (+3 more)

### Community 148 - "Computer Status Poller"
Cohesion: 0.27
Nodes (10): fixture, _settings(), _task(), test_a_result_carrying_an_error_is_marked_failed_even_if_the_box_said_finished(), test_a_still_running_task_is_left_alone(), test_a_task_the_box_reports_finished_is_marked_finished(), test_an_unreachable_box_past_the_stale_window_is_marked_stale(), test_an_unreachable_box_within_the_stale_window_is_left_alone() (+2 more)

### Community 149 - "Sandbox Live Security"
Cohesion: 0.23
Nodes (11): _exec(), CompletedProcess, fixture, Checks that are only meaningful against the deployed pod. Run by hand:…, DoD 10. A token here is a path to the cluster API., DoD 10. Default-deny egress is the boundary, not a mitigation., _require_live(), test_no_eve_environment_variables_are_present_beyond_the_api_key() (+3 more)

### Community 150 - "Suggestion Model Fakes"
Cohesion: 0.17
Nodes (8): FakeModel, Mirrors the surface `suggest` uses: with_structured_output, with_config,…, The failure this prevents: a client rendering chips that were plausible…, A run whose input state omits `member`/`messages` must degrade to no chips, not…, Degrade to no chips, not a KeyError out of the node., test_a_skip_still_clears_the_previous_turns_chips(), test_a_state_missing_member_or_messages_gets_no_chips(), test_openers_survive_a_state_missing_member()

### Community 151 - "UI Surface Assembly"
Cohesion: 0.17
Nodes (9): Assembling a model-authored component tree into a create operation., The model supplies components and nothing else - two fewer fields it can get…, No server-side data source exists, so nothing produces `$data.` bindings.…, Capability gating checks every type the model used, at any depth - a nested…, Runs BEFORE validation, on a tree straight from the model, so it can never…, test_component_types_tolerates_malformed_input(), test_component_types_walks_the_whole_tree(), test_data_is_empty_and_local_state_is_unseeded() (+1 more)

### Community 152 - "Thread Access Control"
Cohesion: 0.20
Nodes (11): create, on, deny_by_default(), Fail closed for everything without an explicit handler below. Runs need no…, stamp_thread_owner(), _ctx(), A create request replaying another member's thread_id must still be checked…, No former carve-out here: aegra-api 0.10.3 never dispatches an auth event with… (+3 more)

### Community 153 - "Image Skill Corpus"
Cohesion: 0.22
Nodes (10): shutil, _docker_available(), eve_image(), fixture, The built image must actually contain the skills corpus. `Settings.skills_dir`…, Pinned against the repo's own directory listing rather than a hardcoded list,…, The end the bug was actually felt at. `ls` proves the files are there; this…, _repo_skill_names() (+2 more)

### Community 154 - "Family Roster Management"
Cohesion: 0.20
Nodes (6): Member, Exception, The family roster: who Eve serves and what each member may do. The roster holds…, Raised when an authenticated subject is not in the roster., Roster order, for the ambient poll loop. Insertion-ordered dict., UnknownMemberError

### Community 155 - "Household Tool Definitions"
Cohesion: 0.29
Nodes (11): list_events(), _member(), RunnableConfig, tool, Read the member's whole wardrobe catalogue, grouped by category. Call this…, Today's forecast for the household., What is on the member's calendar for the rest of today. Requires the…, Catalogue any new photos the member has added to their Immich wardrobe album.… (+3 more)

### Community 156 - "UI Component Schema"
Cohesion: 0.27
Nodes (10): _component(), _component_array(), components_schema(), _properties(), _properties_for(), _property(), The catalog, expressed as the `show_surface` argument schema. ADR 0017 put the…, The value constraints `protocol._validate_property` enforces, said in schema… (+2 more)

### Community 157 - "Memory Registry Fixtures"
Cohesion: 0.18
Nodes (4): _clean_pending(), fixture, No background extraction may leak from one test into the next., recorded()

### Community 158 - "Sandbox Docker Image"
Cohesion: 0.24
Nodes (9): hashlib, subprocess, _docker_available(), fixture, The one test that verifies the *built Docker image* can actually import and run…, Critical 1 regression, at the actual image level. Before the fix, every /invoke…, sandbox_container(), sandbox_image() (+1 more)

### Community 159 - "MCP Server Registry"
Cohesion: 0.29
Nodes (8): mcp, mcp_client_stdio, invoke(), Generic dispatcher for dynamically-discovered MCP tools. A fresh connection per…, Registered MCP servers, by id. Empty in production until a concrete skill needs…, register(), server_params_for(), StdioServerParameters

### Community 160 - "Filter Infrastructure Errors"
Cohesion: 0.24
Nodes (10): FilterError, Exception, The REFLEX call itself could not be completed — a couldn't-decide, not a…, _judge(), A REFLEX outage is a couldn't-decide, not a decided-no (fix round 1, item 2):…, test_a_filter_infrastructure_failure_defers_rather_than_dropping(), _judge(), _judge() (+2 more)

### Community 161 - "Home Assistant Stub"
Cohesion: 0.25
Nodes (8): fastapi, call_service(), get_state(), list_states(), get, post, A minimal stand-in for Home Assistant's REST API, for integration tests that…, HA returns every entity, not just the lights, and each one carries an…

### Community 162 - "Relevance Filter Logic"
Cohesion: 0.33
Nodes (8): langchain_core_exceptions, _household_context(), judge(), load_filter_prompt(), The REFLEX-tier relevance gate: is this worth interrupting anyone over?…, Household memory only. Profile memory is deliberately not read: the audience is…, _render(), _roster_block()

### Community 163 - "Specialist Search Builder"
Cohesion: 0.25
Nodes (9): live, _dot(), rank_skills(), build_skills_search(), search_skills(), BaseTool, Build one search tool scoped to a specialist's own procedures., skipif (+1 more)

### Community 164 - "Ambient Filter Verdicts"
Cohesion: 0.28
Nodes (9): FilterVerdict, BaseModel, Ambient filtering runs on every household signal; it must never spend the…, test_the_reflex_tier_is_the_one_used(), _get_model(), A 3am false alarm is only fixable if it is visible (fix round 1, item 6): every…, test_an_urgent_bypass_is_logged_at_warning_level(), test_replay_ambient_calls_the_real_judge() (+1 more)

### Community 165 - "Model Provider Config"
Cohesion: 0.22
Nodes (8): models, npm, options, apiKey, baseURL, provider, litellm, $schema

### Community 166 - "Skill Ranking Logic"
Cohesion: 0.42
Nodes (8): Skill, _fake_embed(), A scoped skill must not reach Eve's own search - she delegates rather than…, test_eve_never_sees_a_specialist_scoped_skill(), test_rank_skills_prefers_the_closer_match(), test_search_skills_adds_an_mcp_match_to_dynamic_tools(), test_search_skills_caps_dynamic_tools(), test_search_skills_returns_a_procedure_directly()

### Community 167 - "OAuth Token Store"
Cohesion: 0.22
Nodes (5): migrated(), fixture, eve-tools' own pool, against the real Postgres. ADR 0016: eve-tools holds one…, Two members with the same provider must coexist, and one member must not get…, test_the_primary_key_is_provider_plus_member()

### Community 168 - "Dynamic Tool Binding"
Cohesion: 0.22
Nodes (4): test_a_dynamically_bound_tool_is_callable_the_turn_it_is_discovered(), fake_materialize(), test_eve_calls_a_tool_and_returns_the_final_answer(), factory()

### Community 169 - "Detached Span Tracing"
Cohesion: 0.22
Nodes (3): Attributes set on an ended span are silently dropped, and the run's span HAS…, test_a_detached_extraction_records_its_own_span(), ainvoke()

### Community 170 - "Skill Management CLI"
Cohesion: 0.22
Nodes (5): content='' -> "".splitlines() == [] -> [0] raises IndexError unless _render…, The row is the audit trail. forget() is a hard DELETE and is the wrong verb…, test_authored_lists_rules_and_procedures(), test_render_handles_empty_content(), test_revoke_supersedes_and_never_deletes()

### Community 171 - "Wardrobe Management CLI"
Cohesion: 0.39
Nodes (7): _family(), test_a_named_member_is_selected_case_insensitively(), test_an_unknown_member_name_raises(), test_list_prints_the_rendered_wardrobe(), test_sync_reports_an_error_without_crashing(), test_sync_reports_counts_per_member(), test_targets_are_only_members_with_an_album()

### Community 172 - "Health Metric Tools"
Cohesion: 0.32
Nodes (8): get_activity(), get_recovery(), get_sleep(), RunnableConfig, tool, Recovery score, HRV, and resting heart rate for recent days., Sleep duration, stages, and efficiency for recent nights., Training load, calories, steps, and workouts for recent days.

### Community 173 - "Computer Signal Routing"
Cohesion: 0.25
Nodes (7): _computer_signal(), The verdict is synthesised directly from the signal's own member_sub, not…, gates.permitted still runs - a member without computer.use is dropped even…, test_a_computer_signal_is_addressed_only_to_its_own_member(), test_a_computer_signal_never_calls_the_filter(), test_a_computer_signal_records_no_eval_decision(), test_a_computer_signal_still_respects_the_permission_gate()

### Community 174 - "Tool Loop Budget"
Cohesion: 0.25
Nodes (5): LangGraph's own recursion_limit defaults to 10007 and `.compile()` takes no…, The bound is per turn, not per thread: Aegra checkpoints `messages` across…, test_the_loop_budget_resets_on_the_next_turn(), test_the_tool_loop_is_bounded_when_the_model_never_answers(), noop()

### Community 175 - "Skill Documentation Validation"
Cohesion: 0.32
Nodes (7): _documented(), The skill's property table is a FIFTH copy of the catalog, and the only one…, Every `- \x60type\x60: prop, prop` line in the skill body., `rank_skills` embeds `description or name`, so an empty description would make…, test_every_documented_property_matches_the_validator(), test_the_skill_documents_every_component_type(), test_the_skill_has_a_description_for_semantic_ranking()

### Community 176 - "Sandbox Child Process"
Cohesion: 0.33
Nodes (6): contextlib, io, resource, _limit(), main(), The child process. Reads one job on stdin, writes one JSON line on stdout.…

### Community 177 - "Graph Replay Utilities"
Cohesion: 0.29
Nodes (6): _no_extract(), _no_suggest(), The one substitution. See the module docstring., Eval replays score Eve's answer, not her chips. Same reason `_no_extract`…, Invoke the real graph for one member message and return the final text.…, replay_turn()

### Community 178 - "Materialized Tool Calls"
Cohesion: 0.33
Nodes (7): _args_model(), materialize(), _call(), StructuredTool, A tool used once was a wasted approval; Eve should have just done the…, record_invocation(), test_materialized_tool_calls_the_mcp_dispatcher()

### Community 180 - "Thread Digest Summarization"
Cohesion: 0.29
Nodes (3): The thread digest summarises the WHOLE transcript, not just the last exchange -…, test_the_digest_transcript_strips_a_persisted_frame(), ainvoke()

### Community 181 - "Gmail Search Tools"
Cohesion: 0.40
Nodes (6): get_thread(), list_messages(), RunnableConfig, tool, Search Gmail. Gmail query syntax, e.g. 'is:unread from:school'., Read a full Gmail thread by id.

### Community 183 - "Async Extraction Returns"
Cohesion: 0.33
Nodes (3): The point of the whole change: the node returns, the turn ends, and the writes…, test_extract_returns_before_the_work_finishes(), ainvoke()

### Community 184 - "Background Extraction Switch"
Cohesion: 0.33
Nodes (3): The kill switch has to actually switch. With it off the writes must be visible…, test_the_background_flag_off_keeps_extraction_inline(), ainvoke()

### Community 185 - "Reflex Tier Streaming"
Cohesion: 0.33
Nodes (5): Without TAG_NOSTREAM this model's tokens go out on the `messages` channel and…, `get_model` raising - a realistic startup failure, e.g. a missing LiteLLM key -…, test_a_model_factory_failure_yields_no_chips(), test_the_call_is_reflex_tier_and_never_streams(), factory()

### Community 186 - "Database Migration Graph"
Cohesion: 0.40
Nodes (3): alembic_config, alembic_script, The migration graph must have exactly one head. Two branches that each add a…

### Community 187 - "Ntfy Notification Delivery"
Cohesion: 0.40
Nodes (3): _ascii(), Delivery. One protocol, one implementation — swappable as the design asks,…, ntfy carries title and tags in HTTP headers, which are latin-1 on the wire.…

### Community 190 - "Opener Memory Bundling"
Cohesion: 0.40
Nodes (5): _memory(), `_render_memory` narrows the injected bundle to `profile` + `rules` -…, Same narrow bundle as `suggest`, for the same reason: what shapes a plausible…, test_openers_read_profile_and_rules_but_not_household_or_episodic(), test_the_prompt_carries_profile_and_rules_but_not_household_or_episodic()

### Community 191 - "Sync Verification Script"
Cohesion: 0.83
Nodes (3): app_of(), record(), check-sync.sh script

### Community 192 - "Process Reaper Logic"
Cohesion: 0.50
Nodes (3): Process, Wait for the child to actually exit, letting any still-in-flight reader task…, _reap()

### Community 193 - "Notice Delivery Resilience"
Cohesion: 0.50
Nodes (3): (fix round 4, item 5) `deliver` already returned a thread id - the push already…, test_a_record_notice_failure_after_a_successful_delivery_still_marks_seen(), _record_notice()

### Community 195 - "OAuth Token Retrieval"
Cohesion: 0.50
Nodes (3): fixture, Every client call starts by asking the store for an access token., _token()

### Community 201 - "Search Window Logic"
Cohesion: 0.67
Nodes (3): allow_assistant_search(), test_the_search_window_starts_now_and_spans_the_horizon(), _search()

### Community 203 - "Time Window Testing"
Cohesion: 0.67
Nodes (3): parametrize, test_a_window_inside_one_day_does_not_wrap(), test_quiet_hours_wrap_around_midnight()

### Community 205 - "AST Security Integration"
Cohesion: 0.67
Nodes (3): integration, THE assumption test. §6.3 claims the AST check is not what holds the line; this…, test_source_bypassing_the_ast_checker_still_cannot_reach_the_network()

## Knowledge Gaps
- **29 isolated node(s):** `ItemResult`, `ToolProposal`, `models`, `npm`, `apiKey` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1724 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **70 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_settings()` connect `Memory Management` to `Model Hygiene Rules`, `LangGraph Task Dispatch`, `Coding Task Delegation`, `Graph Route Logic`, `Memory Extraction Operations`, `Calendar Signal Sources`, `Computer Settings Configuration`, `Signal Delivery Pipeline`, `Message History Content`, `Coding Session Store`, `Coding Supervisor Decisions`, `Evaluation CLI Tool`, `Family Roster Management`, `Database Migrations`, `Coding Model Dispatch`, `Relevance Filter Logic`, `Ambient Polling App`, `Tool Loop Budget`, `Langfuse Evaluation Replay`, `Tools Client Testing`, `Ntfy Notification Delivery`, `Context Loading Node`, `Database Migration CLI`, `Specialist Agent Testing`, `Notification Delivery Pipeline`, `Skills Registry Loader`, `MCP Tool Registry`, `Shared Test Fixtures`, `Home Assistant Tools`, `Resource Scoping Auth`, `PAT Management CLI`, `Suggestion Node Tests`, `Recall Token Budgets`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Why does `Signal` connect `Signal Processing Fixes` to `Relevance Filter Logic`, `Ambient Notification Mocks`, `Ambient Notification Store`, `Ambient App Testing`, `Ambient Polling App`, `LangGraph Task Dispatch`, `Notification Delivery Pipeline`, `Ambient Audience Filtering`, `Calendar Signal Sources`, `Live Ambient Integration`, `Computer Signal Routing`, `Notification Quiet Hours`, `Signal Delivery Pipeline`, `Computer Task Polling`, `Langfuse Evaluation Replay`, `Notification Idempotency`, `Ambient Pipeline Logic`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `invoke()` connect `Home Assistant Tools` to `Memory Management`, `Model Hygiene Rules`, `Wardrobe Catalog Sync`, `LangGraph Task Dispatch`, `Coding Task Delegation`, `Health Metric Tools`, `Calendar Signal Sources`, `Tools Client Testing`, `Materialized Tool Calls`, `Gmail Search Tools`, `Household Tool Definitions`, `Coding Model Dispatch`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `get_settings()` (e.g. with `conftest.py` and `test_the_tool_loop_is_bounded_when_the_model_never_answers()`) actually correct?**
  _`get_settings()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `Signal` (e.g. with `_handle_in_background()` and `poll_once()`) actually correct?**
  _`Signal` has 36 INFERRED edges - model-reasoned connections that need verification._
- **Are the 49 inferred relationships involving `Family` (e.g. with `oidc()` and `test_a_dev_token_is_still_accepted_alongside_the_pat_path()`) actually correct?**
  _`Family` has 49 INFERRED edges - model-reasoned connections that need verification._
- **What connects `ItemResult`, `ToolProposal`, `models` to the rest of the system?**
  _29 weakly-connected nodes found - possible documentation gaps or missing edges._