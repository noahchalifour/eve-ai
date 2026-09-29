# Graph Report - eve-ai  (2026-09-18)

## Corpus Check
- 352 files · ~421,904 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 14 file(s) not represented in the graph (top: (none) 5, .example 1, .eve-ambient 1)

## Summary
- 4099 nodes · 8180 edges · 245 communities (187 shown, 58 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 983 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `04e7d268`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Memory
- Signal
- Tier
- test_ambient_notify.py
- test_wardrobe_catalog.py
- test_ambient_app.py
- graph.py
- test_acp_app.py
- permission_denial
- _no_extract
- Evaluation Gate Logic
- test_eve_tools_oauth_store.py
- extract.py
- SourcePollError
- Computer Settings Configuration
- repo.py
- Monarch Money Integration
- test_context.py
- suggest.py
- get_pool
- session.py
- ACP Session Management
- test_coding_supervisor.py
- test_ambient_pipeline.py
- get_settings
- eve_computer/app.py
- Database Migrations
- FakeToolCallingModel
- Gmail OAuth Tooling
- test_eval_scorers.py
- get_tools_settings
- tools_client.py
- test_tools_propose.py
- Calendar Source Testing
- Finance Source Testing
- CalDAV Client Testing
- eve_ambient/app.py
- test_ambient_store.py
- datetime
- Graph Capability Testing
- test_ui_schema.py
- asyncio
- test_ambient_sources_computer.py
- Personal Access Tokens
- pending.py
- test_memory_extract.py
- Ambient Filter Testing
- Mail Source Testing
- test_replay_turn_never_runs_extract
- invoke
- Skill Authoring Logic
- test_integration.py
- OAuth Token Refresher
- protocol.py
- test_eve_tools_whoop.py
- Dynamic UI Testing
- check
- run_tool
- UI Action Recognition
- ui/__init__.py
- Health Provider Integration
- Response Chip Generation
- Thread Title Generation
- test_memory_ranking.py
- Memory Integration Tests
- Family
- Skill Serialization Logic
- persist_ui
- Memory Task Concurrency
- Memory Recall Testing
- UI Stream Handshake
- test_a_partial_source_failure_on_an_unprimed_source_does_not_prime_or_notify
- test_auth.py
- Whoop Data Normalization
- test_specialists_base.py
- eve/settings.py
- test_ambient_gates.py
- test_coding_store.py
- Extraction
- EveState
- pipeline_stubs
- overlapping
- test_memory_store.py
- test_the_poll_loop_survives_a_raising_poll_once
- test_coding_live.py
- test_eve_tools_immich.py
- conftest.py
- Ntfy Notification Provider
- httpx
- test_eval_hygiene.py
- call_service
- title.py
- Handoff: emit `tool_labels` from the LangGraph graph
- Oura Client Testing
- Gmail API Client
- poller.py
- Sandbox HTTP API
- auth.py
- Oura API Client
- catalog.py
- Coding Dispatch Tests
- Docker Image Integration
- Vector Embedding Tests
- Suggestion Node Tests
- caldav_client.py
- OAuth Provisioning Server
- test_acp_client.py
- _handle
- Model Catalogue Tests
- Tool Dispatch Routing
- UI Schema Validation
- Claude Computer Use
- Gmail OAuth Setup
- UI Action Protocol
- test_ambient_integration.py
- Agent Registry Management
- ambient_marker
- refresh
- Memory Graph Execution
- test_computer_app.py
- Empty Thread Openers
- UI Component State
- Home Assistant Integration
- embed.py
- Health Provider Fan-out
- logging
- Live Computer Environment
- Recall Token Budgets
- Memory Search Integration
- Health Specialist Tests
- Monarch Money Client
- test_eval_datasets.py
- Specialist Skill Scoping
- Coding Lifecycle Integration
- Gmail API Integration
- Turn Extraction Stamping
- Embedding Budget Management
- test_skills_integration.py
- Wardrobe Specialist Tests
- Sandbox Security Integration
- UI Protocol Streaming
- immich.py
- Wardrobe Asset Store
- _read_capped
- test_specialists_integration.py
- DeliveryError
- test_computer_dispatch.py
- Ambient Coding Sessions
- Computer Status Poller
- Sandbox Live Security
- Suggestion Model Fakes
- UI Surface Assembly
- Thread Access Control
- test_sandbox_docker_image.py
- conn
- stylist.py
- UI Component Schema
- Memory Registry Fixtures
- Decision
- MCP Server Registry
- test_sandbox_app.py
- Home Assistant Stub
- filter.py
- test_the_default_suggest_node_never_leaks_chip_tokens_onto_messages
- FilterVerdict
- Model Provider Config
- test_tool_messages_never_reach_the_extraction_prompt
- OAuth Token Store
- Dynamic Tool Binding
- Detached Span Tracing
- pytest
- get_budgets
- Health Metric Tools
- Computer Signal Routing
- Tool Loop Budget
- test_skills_build_a_ui.py
- Sandbox Child Process
- strip_frames_from_content
- test_skills_materialize.py
- Ambient Source Registry
- Thread Digest Summarization
- send_email
- Procedure Extraction Guards
- test_suggest_runs_after_extract
- Background Extraction Switch
- Reflex Tier Streaming
- Database Migration Graph
- _with_frame
- Malformed Filter Handling
- test_zero_digest_cadence_does_not_break_a_completed_turn
- Opener Memory Bundling
- Sync Verification Script
- test_a_null_expiry_never_refreshes
- test_a_token_inside_the_skew_window_refreshes
- test_refresh_now_refreshes_even_a_fresh_token
- test_the_schema_survives_conversion_to_a_tool_call
- Recall Cancellation Handling
- Extraction Sequence Invariants
- Project Documentation
- Test Infrastructure Services
- Search Window Logic
- Environment Bootstrap Scripts
- _kill_group
- Authentication Fixtures
- AST Security Integration
- Subprocess Error Handling
- Image Publication Scripts
- Tool Execution Sandbox
- Tool Authoring Phase
- test_a_missing_row_raises_not_connected
- Notification Idempotency
- Execution Budget Management
- Ambient Turn Logic
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

## Communities (245 total, 58 thin omitted)

### Community 0 - "Memory"
Cohesion: 0.09
Nodes (40): One recall, at creation, snapshotted onto the row for every later supervisor…, _recall_context(), apply_duplicates(), Supersede each loser by its keeper. Returns how many were retired., Eve's memory (Phase 2). Four layers - profile, household, episodic, digest - in…, _budget_seconds(), _cancel_and_await(), _embed_within_budget() (+32 more)

### Community 1 - "Signal"
Cohesion: 0.09
Nodes (21): The polled-source registry. `home` is absent deliberately: it is pushed, not…, Source, Signal, _fake_source(), _poll(), (fix wave item 1) Priming exists to swallow a pre-existing backlog the first…, (fix round 4, item 2 follow-up) Once a source is already primed, a persistent…, An expired token for one member must not throw away the signals already… (+13 more)

### Community 2 - "Tier"
Cohesion: 0.06
Nodes (54): BaseChatModel, langchain_agents, langchain_core_language_models, langchain_core_runnables, langchain_core_tools, langchain_openai, langgraph_errors, opentelemetry (+46 more)

### Community 3 - "test_ambient_notify.py"
Cohesion: 0.08
Nodes (53): ambient_settings(), FakeClient, FakeRuns, FakeThreads, fixture, parametrize, RaisingNotifier, Aegra scopes threads to the authenticated identity, so this header is the whole… (+45 more)

### Community 4 - "test_wardrobe_catalog.py"
Cohesion: 0.06
Nodes (46): start_as_current_span(), _fake_invoke(), FakeSpan, FakeTracer, _item(), _patch_common(), _insert(), Stands in for eve.tools_client.invoke, which returns a JSON STRING. (+38 more)

### Community 5 - "test_ambient_app.py"
Cohesion: 0.06
Nodes (26): _clear_background_tasks(), client(), fixture, (fix round 4, item 11) An operator otherwise cannot tell a wrong secret from an…, (fix round 4, item 1) The autouse `settings` fixture sets…, The other direction of fix round 4 item 1: a deployment with ambient enabled…, (fix round 4, item 4) By design no `eve_ambient_seen` row exists until a signal…, (fix round 4, item 4) One leaked secret must not buy unbounded concurrent… (+18 more)

### Community 6 - "graph.py"
Cohesion: 0.09
Nodes (33): langgraph_graph, pydantic, build_graph(), eve(), tools_node(), _handle_tool_error(), _live_specs(), _persona_message() (+25 more)

### Community 7 - "test_acp_app.py"
Cohesion: 0.13
Nodes (7): client(), _key(), fixture, The door, not the machinery. `session` is mocked wholesale: Task 4 owns the…, The GUI lane's single worker is the whole reason it exists. A change that lets…, test_an_unknown_agent_is_a_400_not_a_500(), test_the_gui_task_queue_is_untouched()

### Community 8 - "permission_denial"
Cohesion: 0.09
Nodes (25): langgraph_prebuilt, langgraph_types, dispatch_computer_task(), InjectedState, RunnableConfig, tool, dispatch_computer_task: Eve's one tool onto her own machine. Permission is…, Dispatch a task to Eve's own computer: a persistent Linux desktop with a… (+17 more)

### Community 9 - "_no_extract"
Cohesion: 0.09
Nodes (31): _fake_factory(), _no_extract(), _no_recall(), _no_suggest(), The whole point of the route. A client showing an empty chat wants chips, not…, An empty chat asking for openers must stay an empty chat. A message appended…, The route is opt-in. Every existing client and every graph consumer that never…, Fails CLOSED: the flag stops Eve answering, so anything but an explicit `True`… (+23 more)

### Community 10 - "Evaluation Gate Logic"
Cohesion: 0.06
Nodes (31): _fake_duplicate_pair(), _fake_rule(), _hygiene_args(), A harness that can silently spend the month's budget is one that will., Stub every collaborator `_cmd_hygiene` calls so the test asserts only on the…, Report-only by default (docs/architecture.md): a bare `hygiene --member X` must…, --apply without EVE_EVAL_HYGIENE_APPLY_ENABLED must not supersede or prune -…, Ten points is above the noise floor of a nondeterministic replay. (+23 more)

### Community 11 - "test_eve_tools_oauth_store.py"
Cohesion: 0.15
Nodes (7): The token store. Provider-agnostic on purpose: a third wearable should be a new…, The upsert is what makes rotation safe to repeat. A second INSERT would violate…, An Oura PAT has no refresh token and no expiry. Spec 1.1 - this must not need a…, health.py's `unconfigured` list is compared in tests; an unstable order there…, test_a_non_rotating_credential_is_an_ordinary_row(), test_configured_providers_is_sorted(), test_save_twice_updates_rather_than_duplicating()

### Community 12 - "extract.py"
Cohesion: 0.07
Nodes (49): HumanMessage, mark_replied(), A member speaking in an ambient thread IS the label (eval design 5). No lookup…, apply_operations(), extract(), _filter_authored(), _is_single_sentence(), load_extract_prompt() (+41 more)

### Community 13 - "SourcePollError"
Cohesion: 0.10
Nodes (34): poll(), Calendar events as signals, via eve-tools' CalDAV client. Two signal shapes per…, True only when the event's start is beyond the lookahead - not yet imminent,…, _starts_later(), _starts_soon(), _budget_overruns(), _is_number(), _parsed_date() (+26 more)

### Community 14 - "Computer Settings Configuration"
Cohesion: 0.07
Nodes (38): Any, BaseSettings, Settings, _clear(), fixture, test_a_short_computer_api_key_is_rejected(), test_computer_is_disabled_by_default(), test_enabling_with_a_long_enough_key_is_accepted() (+30 more)

### Community 15 - "repo.py"
Cohesion: 0.10
Nodes (41): add_worktree(), _base_ref(), clone_path(), _clone_url(), ensure_clone(), GitError, publish(), Exception (+33 more)

### Community 16 - "Monarch Money Integration"
Cohesion: 0.08
Nodes (35): _current_month(), _fake_client(), _FakeMonarch, fixture, The query asks for `name`, so this should be unreachable against a real account…, Deriving the expected month through the same datetime.now() call the normalizer…, Enough of MonarchMoney to observe how it was authenticated., A Monarch account created through Google sign-in has no password, so a session… (+27 more)

### Community 17 - "test_context.py"
Cohesion: 0.11
Nodes (34): build_member_context(), build_system_prompt(), datetime, _section(), MemberContext, TypedDict, _bundle(), _mem() (+26 more)

### Community 18 - "suggest.py"
Cohesion: 0.11
Nodes (28): Span, last_exchange(), The last thing the member said and the last thing Eve said, as plain strings.…, _budget_seconds(), clean(), _emit(), load_openers_prompt(), load_suggest_prompt() (+20 more)

### Community 19 - "get_pool"
Cohesion: 0.06
Nodes (51): psycopg_rows, psycopg_types_json, advance_cursor(), bump_supervisor_turns(), create_session(), get(), live_sessions(), live_sessions_for() (+43 more)

### Community 20 - "session.py"
Cohesion: 0.12
Nodes (22): Client, PermissionOption, ReadTextFileResponse, RequestPermissionResponse, Any, Path, SessionClient, _chunk_text() (+14 more)

### Community 21 - "ACP Session Management"
Cohesion: 0.15
Nodes (25): create(), get(), kill(), send(), snapshot(), Turn, fake_spawn(), FakeConn (+17 more)

### Community 22 - "test_coding_supervisor.py"
Cohesion: 0.19
Nodes (26): Returns the sessions that resolved - finished, failed, blocked, or stale - on…, tick(), _box(), fixture, The four-way decision, and what each outcome does to the row. The test that…, The subprocess and the worktrees stay up so the member's answer can resume this…, It is waiting on a human. Asking again every 20 seconds would be a notification…, The spec's per-session wall-clock bound. Without it a session parked on… (+18 more)

### Community 23 - "test_ambient_pipeline.py"
Cohesion: 0.10
Nodes (33): (fix wave item 1) A computer signal bypasses quiet hours exactly like an urgent…, (fix wave item 1) A member already at the daily cap still gets a computer…, A 3am false alarm is only fixable if it is visible (fix round 1, item 6): every…, Design section 9: the trace only starts at the compose turn, so the verdict,…, The cooldown is both the most frequently taken path and, before this fix, the…, The case none of the fix-round-1 tests covered (fix round 2, item 1): a member…, The accepted trade-off from fix round 2, item 1: if a defer persists longer…, fix round 2, item 3: once the idempotence skip can fire for every member in the… (+25 more)

### Community 24 - "get_settings"
Cohesion: 0.07
Nodes (56): random, check_ceiling(), _cmd_build(), _cmd_gate(), _cmd_hygiene(), _cmd_run(), main(), `eve-eval`: build datasets, run them, gate on regressions, report hygiene. Runs… (+48 more)

### Community 25 - "eve_computer/app.py"
Cohesion: 0.14
Nodes (28): delete, fastapi_responses, Queue, enqueue(), A family member's interjection. Recorded, never delivered by the box: Eve…, _check_auth(), close_session_route(), create_session_route() (+20 more)

### Community 26 - "Database Migrations"
Cohesion: 0.07
Nodes (6): alembic, Alembic environment for Eve's own schema. Two things here are load-bearing and…, run_migrations_offline(), run_migrations_online(), _url(), sqlalchemy

### Community 27 - "FakeToolCallingModel"
Cohesion: 0.14
Nodes (14): GenericFakeChatModel, importlib, FakeToolCallingModel, `GenericFakeChatModel` raises `NotImplementedError` from `bind_tools` - fine…, factory(), test_ask_finances_reads_transactions_through_eve_tools(), test_ask_home_calls_get_state_through_eve_tools(), The shape a real model takes now that `list_entities` exists: one call returns… (+6 more)

### Community 28 - "Gmail OAuth Tooling"
Cohesion: 0.07
Nodes (21): importlib_util, parametrize, `scripts/gmail_oauth_setup.py` is operator tooling, not part of the package, so…, Re-running is the normal way to refresh a token, and it must not cost anyone…, The caller writes the returned object under a compare-and-set; the value it…, Better to make the operator look than to replace a blob we cannot read with one…, test_a_non_object_refuses_the_write(), test_an_empty_or_blank_value_is_treated_as_no_members() (+13 more)

### Community 29 - "test_eval_scorers.py"
Cohesion: 0.10
Nodes (26): Judgement, _pct(), BaseModel, Fraction of assertions the judge marked satisfied, plus the canary. An item…, Exact comparison against the recorded verdict, plus reply precision.…, score_ambient(), score_turns(), _ambient() (+18 more)

### Community 30 - "get_tools_settings"
Cohesion: 0.12
Nodes (19): psycopg_pool, _check_auth(), healthz(), invoke_tool(), InvokeRequest, BaseModel, get, post (+11 more)

### Community 31 - "tools_client.py"
Cohesion: 0.18
Nodes (20): check_coding_session(), delegate_coding_task(), RunnableConfig, tool, Eve's three coding tools. Permission is checked here, before the HTTP call, so…, What Eve's delegated coding sessions are doing right now. Use when a member…, Pass a member's message into a running coding session - a correction, a…, Delegate a coding task to a dedicated coding agent working in a real git… (+12 more)

### Community 32 - "test_tools_propose.py"
Cohesion: 0.07
Nodes (33): live_tools(), Approved and not revoked. Read on every search_skills call., _enabled(), _noop(), fixture, THE test that matters most. _handle_tool_error degrades every tool exception to…, If you cannot approve, you cannot propose. There is no queue., Eve revises before a human is bothered. (+25 more)

### Community 33 - "Calendar Source Testing"
Cohesion: 0.10
Nodes (29): _invoke_returning(), The horizon is wider than the lookahead precisely so a change to a far-off…, The filter reads only the one-line summary, so a `:rev:` summary asserting an…, The filter prompt renders `occurred_at` as "Occurred at"; a future start time…, A model should never be handed "…, starting None." (fix round 1 item E) -…, (fix round 4, item 2) If this source swallowed eve-tools' `error:` string into…, (rereview fix, item 2) `caldav_client.list_events` isolates a single failing…, (fix round 4, item 6) `_starts_soon` had no lower bound, so it answered true… (+21 more)

### Community 34 - "Finance Source Testing"
Cohesion: 0.11
Nodes (27): _fake_invoke(), _invoke(), The other direction: transactions fail, budgets succeed. Symmetric with the…, .replace(tzinfo=UTC) on an offset-bearing timestamp would silently shift the…, `{"transactions": 5}` is truthy, so `or []` never fires; the `for` statement…, `{"budgets": 5}` is truthy and non-iterable, so `or []` never fires and the…, The real Monarch payload nests merchant as {"name", "id", ...}, not a flat…, Nothing has happened. A signal per budget per poll would burn the filter's… (+19 more)

### Community 35 - "CalDAV Client Testing"
Cohesion: 0.10
Nodes (24): FakeEvent, _ics(), one_calendar(), fixture, The caldav library is synchronous and talks to a real server, so these tests…, `principal().calendars()` returns every collection the member owns - task…, A VEVENT that parses cleanly can still carry a property shape that breaks the…, (rereview fix, item 2) This test used to assert that a missing credential… (+16 more)

### Community 36 - "eve_ambient/app.py"
Cohesion: 0.06
Nodes (48): collections, Request, _audience_for(), _handle_in_background(), healthz(), home_assistant_signal(), lifespan(), _poll_forever() (+40 more)

### Community 37 - "test_ambient_store.py"
Cohesion: 0.08
Nodes (17): pool(), fixture, Integration tests against the real Postgres in docker-compose.test.yml., (fix round 4, item 8) `prune_seen` used to delete any row past the horizon,…, This is what makes the first poll prime rather than notify., `(source, key)` is not unique over time - the same key legitimately re-fires…, The other half of the bound: a notice sent shortly after its decision (the…, A door that was open six hours ago and is open again is news again. (+9 more)

### Community 38 - "datetime"
Cohesion: 0.17
Nodes (12): datetime, poll(), Resolved coding sessions as signals. Two deliberate deviations from how other…, _summary(), Home Assistant state changes as signals. Pushed, never polled: which entities…, Shapes only. No I/O, no behaviour beyond parsing one string., skipif, One fabricated Home Assistant signal, all the way through: a real REFLEX… (+4 more)

### Community 39 - "Graph Capability Testing"
Cohesion: 0.09
Nodes (17): _bound_types(), _declaring(), Pins the one behavior this task exists to guarantee: a thread checkpointed…, The seam exists for tests and eval. The DEFAULT must be the real node, or the…, The client already sends its catalog. Spending it only on a post-hoc refusal is…, Intersected with the server catalog, so a client advertising a type this server…, `capabilities()` only checks that `assistant_ui` is a dict. A non-list…, `search_skills` returns a Command (it updates `dynamic_tools`); a data tool… (+9 more)

### Community 40 - "test_ui_schema.py"
Cohesion: 0.12
Nodes (23): langchain_core_utils_function_calling, _branches(), _component_items(), full(), fixture, `eve.ui.schema` is a PROJECTION of the server validator, not a sixth hand-…, It rides in context on every turn a capable client is connected. The ceiling is…, OpenAI's structured-output subset supports `anyOf` and rejects `oneOf`. The two… (+15 more)

### Community 41 - "asyncio"
Cohesion: 0.08
Nodes (43): argparse, asyncio, _run(), close_pool(), main(), _run(), migrate(), Connection pool and schema migration for Eve's own tables. Migrations run… (+35 more)

### Community 42 - "test_ambient_sources_computer.py"
Cohesion: 0.14
Nodes (9): _no_recently_resolved_by_default(), fixture, Every test in this file that doesn't care about the 24-hour re-derivation…, A task that resolved on this exact tick appears in both `sync()`'s return value…, Not "never recurs": a suppressed or deferred signal must get a real retry…, The scenario the fix wave describes: `poller.sync()` only returns a row on the…, test_a_task_in_both_sync_and_recently_resolved_is_not_duplicated(), test_a_task_missed_by_this_ticks_sync_is_still_recovered() (+1 more)

### Community 43 - "Personal Access Tokens"
Cohesion: 0.11
Nodes (23): clean_pats(), fixture, integration, Personal access tokens: minting, resolution, and revocation. The unit tier…, The whole reason this table exists rather than one shared secret., Otherwise a typo mints a token that 401s on first use with no clue why - the…, Revocation is by label, so two live tokens sharing one would make `revoke`…, Every OIDC request presents a JWT. If resolution did not short-circuit on the… (+15 more)

### Community 44 - "pending.py"
Cohesion: 0.09
Nodes (24): Process, create(), get(), In-memory task state for the box. Not durable across a restart - eve-ambient's…, Task, clear(), drain(), _hold() (+16 more)

### Community 45 - "test_memory_extract.py"
Cohesion: 0.13
Nodes (23): Operation, Catch a structured-model add that would turn one durable row into two facts., A kid cannot author a rule that changes how Eve treats the family., Catch a subject stored differently from lowercased search tokens., Profile and household are injected in full and never vector searched., `eve.suggest` imports this. A leading underscore across a module boundary is a…, test_a_rule_op_is_dropped_when_authoring_is_disabled(), test_a_rule_operation_is_written() (+15 more)

### Community 46 - "Ambient Filter Testing"
Cohesion: 0.11
Nodes (20): FakeStructuredModel, no_household_memory(), _load_always_on(), fixture, Final round, item 1: `langchain_core`'s `OutputParserException` (unknown tool…, Belt-and-braces (fix round 2, item 2, the related gap): if…, Without it the filter re-tells the family things they already know., Postgres being unreachable should cost the filter its context, not its ability… (+12 more)

### Community 47 - "Mail Source Testing"
Cohesion: 0.08
Nodes (20): int(x)/1000 on a huge string overflows datetime.fromtimestamp with…, gmail.py's hydration fills a missing header with "", present but falsy - not…, The exact defect this closes: a truthy non-list value passes `or []` unscathed…, `{"messages": 5}` is truthy, so `or []` never fires; the `for` statement itself…, The filter reads `summary` and nothing else, so the one line has to carry…, (fix round 4, item 2) eve-tools returns error strings rather than raising, and…, tools_client.invoke hands back the already-unwrapped result as JSON., A payload that legitimately owns a `result` key (unlikely here, but tool_result… (+12 more)

### Community 48 - "test_replay_turn_never_runs_extract"
Cohesion: 0.17
Nodes (6): _fake_factory(), factory(), Mirrors replay_ambient's posture: an unknown member sub (or any other graph…, An eval run that writes memory corrupts the thing it is measuring., test_replay_turn_never_runs_extract(), test_replay_turn_reports_an_error_rather_than_raising()

### Community 49 - "invoke"
Cohesion: 0.11
Nodes (32): dispatch_task(), get_computer_task(), invoke(), One door to eve-tools, and since Phase 5c one to eve-sandbox. Two targets…, POST /tasks on eve-computer. Not routed through `invoke()`: the box's task API…, GET /tasks/{id} on eve-computer. `None` means the box could not be asked at all…, fixture, mock (+24 more)

### Community 50 - "Skill Authoring Logic"
Cohesion: 0.11
Nodes (16): _proc(), A procedure Eve wrote once and can never revise goes stale and stays stale. The…, The core invariant of this phase: a turn whose last human message carries the…, The guard must refuse the ambient turn without refusing every turn - a guard…, Global constraint: a tool returns an error string, never raises. A raise here…, test_parse_skill_text_falls_back_without_frontmatter(), test_write_skill_adds_a_procedure_row(), add() (+8 more)

### Community 51 - "test_integration.py"
Cohesion: 0.12
Nodes (19): _client(), End-to-end integration tests against a live `aegra serve` instance. Requires…, A family member must not be able to delete another member's thread. Same…, Starting a run on the owner's own thread must not be denied by the…, Dispatch one turn and wait for the run to reach a terminal state. `runs.join`…, A dispatched turn must actually execute `load_context` under Aegra and reach…, The whole point of the credential: a scripted client connects with a minted PAT…, A bare `pytest.raises(Exception)` would also pass on a network hiccup or a… (+11 more)

### Community 52 - "OAuth Token Refresher"
Cohesion: 0.14
Nodes (23): Refresher, get_pool(), AsyncConnectionPool, access_token(), configured_providers(), get_row(), _is_stale(), NotConnected (+15 more)

### Community 53 - "protocol.py"
Cohesion: 0.19
Nodes (19): append_frame(), _byte_length(), _compact(), frame(), The server side of the `assistant-ui/1.0` wire contract. A mirror of the…, `None` when `operation` is a legal create/patch/delete, otherwise the same…, The client retains only JSON-safe values, with every string under the…, The portable frame: one open marker, one JSON object per line, one close… (+11 more)

### Community 54 - "test_eve_tools_whoop.py"
Cohesion: 0.12
Nodes (24): fixture, mock, parametrize, WHOOP v2 client and normalizers. Every test fakes HTTP with respx and the token…, A UTC calendar date is not an acceptable substitute for provider time., Every client call starts by asking the store for an access token., _recovery_record(), test_a_401_refreshes_once_and_retries() (+16 more)

### Community 55 - "Dynamic UI Testing"
Cohesion: 0.08
Nodes (19): fixture, `show_surface`: the model's entire share of the dynamic UI feature., The reported bug's SECOND failure. A model that fixed its invented type names…, `stream.emit` returns False outside a runnable context. Returning the artifact…, Properties are sorted, so the hint is stable across runs - a model retrying…, An unknown type is already rejected as `component-type`; the hint must not…, The catalog reaches the model through the ARGUMENT SCHEMA now…, The fix for the reported bug, stated as an invariant: a model that reads only… (+11 more)

### Community 56 - "check"
Cohesion: 0.15
Nodes (20): ast, dataclasses, check(), _import_allowed(), The AST allowlist. NOT A SECURITY BOUNDARY. A determined bypass of an AST…, `json.decoder` is fine if `json` is allowed; `urllib.request` is not allowed by…, CheckResult, ToolProposal (+12 more)

### Community 57 - "run_tool"
Cohesion: 0.14
Nodes (26): _package_root(), The directory containing the eve_sandbox package, so the child (run with -P -s,…, run_tool(), No environment variables cross the boundary. A tool that could read them could…, Critical 2 regression. A tool the AST checker fully accepts (no denied import,…, Important 3 regression. The runner writes its JSON result to the same stdout a…, Fix-wave re-review residual finding 1. A tool that writes more than…, The database and the caller disagree about approved bytes. A tampering signal,… (+18 more)

### Community 58 - "UI Action Recognition"
Cohesion: 0.16
Nodes (22): parse_action(), The decoded envelope, or None when `text` is ordinary member speech. Never…, _encoded(), Recognising a UI tap in what arrives as ordinary user text., Same id, so `add_messages` REPLACES rather than appends: the raw envelope would…, The client wraps unconditionally on every provider, LangGraph included -…, Tolerated so a future client that drops the markers on a native channel still…, Never guess. A half-arrived envelope has to become a normal Eve turn, not a… (+14 more)

### Community 59 - "ui/__init__.py"
Cohesion: 0.10
Nodes (25): langgraph_config, capabilities(), emit(), RunnableConfig, The capability handshake in, `custom`-mode frames out. The only module in this…, The client's `DynamicUiCapabilities.toJson()`, or None. `config.configurable`,…, Whether the client declared EVERY id in `catalog_ids`. Fails CLOSED. A set…, Validate, then write one operation to the `custom` stream. `{"assistant_ui":… (+17 more)

### Community 60 - "Health Provider Integration"
Cohesion: 0.13
Nodes (19): fixture, parametrize, The fan-out layer: which providers a member has, merging their answers,…, Spec 4: 1..14, enforced here, not by the caller. A model that asks for 900 days…, The one failure that must NOT degrade to an empty list. Broken auth reported as…, Both clients plus the store, with recorded calls., Spec 4: the specialist reports both rather than silently preferring one. Two…, stub() (+11 more)

### Community 61 - "Response Chip Generation"
Cohesion: 0.13
Nodes (23): _install(), The call succeeded and returned something unusable. Deterministic dead end - no…, The Flutter client reads `custom`, not state. Both exits come from one helper…, An empty list means 'clear the chips', which a client can only act on if it…, A direct call outside a runnable context - exactly what this test does - makes…, The tools loop gave up. That is not a conversation to offer continuations of., No human message means there is no member utterance to continue - the same…, Messages present, so the ambient/empty-human skip does not fire, but `member`… (+15 more)

### Community 62 - "Thread Title Generation"
Cohesion: 0.16
Nodes (10): _config(), FakeModel, _state(), test_generate_replaces_aegra_first_message_fallback(), test_generate_skips_titled_or_ambient_threads(), test_generate_swallows_model_and_update_failures(), test_generate_writes_a_reflex_title(), get_thread() (+2 more)

### Community 63 - "test_memory_ranking.py"
Cohesion: 0.11
Nodes (25): math, estimate_tokens(), fit_budget(), fuse(), Pure ranking maths. No I/O, no settings lookups - everything is an argument, so…, Exponential decay, computed at read time rather than cached. A `decayed_score`…, Reciprocal-rank fusion over ranked id lists. RRF rather than score…, Four characters per token. ponytail: a knob whose job is to stop the prompt… (+17 more)

### Community 64 - "Memory Integration Tests"
Cohesion: 0.15
Nodes (21): requires_litellm_key, clean_memory(), fixture, End-to-end memory through a live `aegra serve`. The unit tests prove each part…, An empty day-one store must produce a complete turn, not an exception., DoD item 2. `extract` decides *when* to supersede via the REFLEX model…, DoD item 3. As with supersession, the natural-language "forget that" trigger…, DoD items 4 and 5, against the real deployed LiteLLM proxy. Gemini was… (+13 more)

### Community 65 - "Family"
Cohesion: 0.14
Nodes (14): Family, Path, Roster order, for the ambient poll loop. Insertion-ordered dict., oidc(), fixture, fixture, The poll loop walks the roster per member; without this it would have to reach…, A guard, not a roster snapshot: if this grant were ever dropped from… (+6 more)

### Community 66 - "Skill Serialization Logic"
Cohesion: 0.11
Nodes (15): SKILL.md's on-disk shape, so parse_skill_text round-trips it. Built with…, serialize_procedure(), A colon-space in the description ('...sitter: call Sam first.') is exactly the…, test_serialize_round_trips_a_description_containing_a_colon(), test_serialize_round_trips_through_the_shared_parser(), Mirrors test_load_always_on_omits_rules_by_default for the procedure layer:…, Fail closed: the kill switch must hold even against a spec the DB still lists…, test_load_skills_parses_an_authored_row_like_a_file() (+7 more)

### Community 67 - "persist_ui"
Cohesion: 0.16
Nodes (18): _is_operation(), persist_ui(), `{}` for every turn that emitted no surface - which is nearly all of them., A dynamically-materialized tool may set an artifact for its own reasons; only a…, _create(), `custom` frames are streamed and never stored. This is what makes a card…, The protocol's per-turn ceiling. The ninth create in one frame makes the client…, The common case. Every non-weather turn must return an empty update, or every… (+10 more)

### Community 68 - "Memory Task Concurrency"
Cohesion: 0.09
Nodes (12): _clean_registry(), fixture, The budget bounds the WAIT, not the work. A slow extraction must still land -…, Two runs can overlap on one thread - Aegra does not prevent it. The older task…, Regression for the bug where `join` only ever looked up the newest task per…, test_a_failing_task_does_not_raise_into_join(), test_a_second_spawn_does_not_strand_the_first(), test_an_anonymous_task_is_still_awaited_by_drain() (+4 more)

### Community 69 - "Memory Recall Testing"
Cohesion: 0.13
Nodes (15): A resumed run can reach recall with no new human turn. Embedding an empty…, The previous turn's writes must be visible to this turn's reads, or 'what did I…, A degraded turn is a complete turn. If the previous extraction is wedged, this…, The load-bearing property of the whole design. An untested degrade path does…, _state(), test_a_failing_embedding_degrades_rather_than_raising(), test_a_slow_embedding_degrades_to_lexical_rather_than_failing(), test_a_stalled_extraction_does_not_hang_the_turn() (+7 more)

### Community 70 - "UI Stream Handshake"
Cohesion: 0.13
Nodes (19): _config(), _create(), The capability handshake in, and `custom`-mode frames out., Per-type gating rather than a version check is what keeps a phone on an old…, A tree of zero components is degenerate but not a capability failure -…, LangGraph indexes run metadata and rejects a non-scalar value there, so the…, A client that declared nothing cannot render anything. Emitting at it would put…, `get_stream_writer()` raises outside a runnable context. Every caller is a tool… (+11 more)

### Community 71 - "test_a_partial_source_failure_on_an_unprimed_source_does_not_prime_or_notify"
Cohesion: 0.09
Nodes (13): A month of calendar entries must not become a month of notifications., An empty inbox, no transactions yet, nothing in the calendar window: the first…, (fix round 4, item 2)…, (fix round 4, item 2 follow-up) A source that raises `SourcePollError` carrying…, (fix round 4, item 2, unattended-operation note) An empty first poll still…, test_a_failing_signal_does_not_stop_its_siblings(), test_a_partial_source_failure_on_an_unprimed_source_does_not_prime_or_notify(), test_an_empty_first_poll_still_primes_so_the_next_real_signal_notifies() (+5 more)

### Community 72 - "test_auth.py"
Cohesion: 0.08
Nodes (52): cryptography_hazmat_primitives, cryptography_hazmat_primitives_asymmetric, jwt, authenticate(), AuthError, extract_bearer(), Raised for any failure to authenticate. Subclasses the SDK's HTTPException so…, Pull the bearer token out of headers whose keys/values may be bytes. (+44 more)

### Community 73 - "Whoop Data Normalization"
Cohesion: 0.23
Nodes (20): _cycle_dates(), _get(), get_activity(), get_recovery(), get_sleep(), _hours(), _newest_first(), _num() (+12 more)

### Community 74 - "test_specialists_base.py"
Cohesion: 0.14
Nodes (19): build_specialist(), BaseTool, _AgentStub, _factory_with(), get_widget(), tool, The ChatGPT backend rejects plain system messages outright - live- verified…, A model that emits `rounds` tool calls before answering. (+11 more)

### Community 75 - "eve/settings.py"
Cohesion: 0.06
Nodes (51): functools, Protocol, day_start_utc(), in_quiet_hours(), local_now(), parse_window(), permitted(), datetime (+43 more)

### Community 76 - "test_ambient_gates.py"
Cohesion: 0.09
Nodes (27): fixture, parametrize, A typo in configuration must not silence Eve permanently and must not raise…, Two members in two zones have two different days; one cap counted in UTC would…, gates.py fails closed on an unmapped source. Without this entry every delegated…, Private correspondence is not the filter's to redistribute, and no permission…, A family calendar is shared logistics: a kid's game on one calendar is news for…, The filter names subs; a hallucinated one must not kill the tick. (+19 more)

### Community 78 - "Extraction"
Cohesion: 0.13
Nodes (10): Extraction, BaseModel, The point of the whole change: the node returns, the turn ends, and the writes…, `persist_ui` (`eve.ui.persist`) writes this turn's `<assistant-ui>` frame into…, test_extract_returns_before_the_work_finishes(), ainvoke(), test_extraction_asks_the_model_about_overlapping_memories(), ainvoke() (+2 more)

### Community 79 - "EveState"
Cohesion: 0.07
Nodes (51): Command, InjectedToolCallId, langgraph_graph_message, live, Static metadata for registered MCP servers - name, description, and argument…, register(), registered_mcp_tools(), _load_skill_md() (+43 more)

### Community 80 - "pipeline_stubs"
Cohesion: 0.10
Nodes (15): pipeline_stubs(), _judge(), fixture, (fix round 4, item 5) `deliver` already returned a thread id - the push already…, Replace the three I/O seams — store, filter, notify — and keep the real gates,…, A REFLEX outage is a couldn't-decide, not a decided-no (fix round 1, item 2):…, A self-contained set of stubs for the eval-recording tests below: freshness,…, test_a_filter_infrastructure_failure_defers_rather_than_dropping() (+7 more)

### Community 81 - "overlapping"
Cohesion: 0.16
Nodes (10): now(), Same guard, the other erasure path: supersede replaces a rule's content just as…, The rule-erasure guard must not spill over onto ordinary fact maintenance.…, test_a_forget_targeting_a_non_rule_fact_still_works_on_an_ambient_turn(), ainvoke(), overlapping(), test_a_supersede_targeting_a_rule_is_refused_on_an_ambient_turn(), ainvoke() (+2 more)

### Community 82 - "test_memory_store.py"
Cohesion: 0.04
Nodes (45): _insert(), pool(), fixture, Integration tests against the real Postgres in docker-compose.test.yml. The…, The isolation that matters most in this whole phase. A profile fact is the most…, Entity matching is the arm that carries names, and names are most of family…, Rows are written before they are embedded, and the embedding call can fail. A…, `load_always_on` already proves member isolation for profile facts; the… (+37 more)

### Community 83 - "test_the_poll_loop_survives_a_raising_poll_once"
Cohesion: 0.15
Nodes (13): Exception, Raised by a patched `asyncio.sleep` to end an otherwise-infinite…, (fix round 4, item 10) `/healthz` used to carry no functional signal at all, so…, The headline claim of this service: a tick that fails outright does not stop…, _StopLoop, test_healthz_reports_the_last_ticks_timestamp_and_counts(), _fake_sleep(), _poll_once() (+5 more)

### Community 84 - "test_coding_live.py"
Cohesion: 0.15
Nodes (12): fastapi_testclient, box(), fixture, parametrize, skipif, The only tier entitled to claim an agent x model pair works. ADR 0004's…, Catches a model retirement (ADR 0004: `gpt-5.4` retired 2026-08-31) before it…, A `wire_api` or provider-block mistake surfaces here and nowhere else. The goal… (+4 more)

### Community 85 - "test_eve_tools_immich.py"
Cohesion: 0.27
Nodes (7): fixture, _settings(), test_album_assets_is_capped_and_marked_truncated(), test_album_assets_returns_ids_and_filenames(), handler(), test_asset_image_returns_base64_and_content_type(), _transport()

### Community 86 - "conftest.py"
Cohesion: 0.08
Nodes (22): hashlib, langchain_core_language_models_fake_chat_models, langgraph_sdk, os, A manual REPL for talking to Eve, streaming her reply token by token. Points at…, signal, One subprocess per call. No reuse, no warm pool. No pool because process…, subprocess (+14 more)

### Community 87 - "Ntfy Notification Provider"
Cohesion: 0.22
Nodes (17): NtfyNotifier, ntfy_settings(), fixture, mock, ntfy carries metadata in headers, which are latin-1 on the wire. Eve's body may…, click_url is built from EVE_AMBIENT_THREAD_URL_TEMPLATE, a configuration value…, ntfy being down must lose the push, not the turn that produced it., test_a_failing_push_returns_false_rather_than_raising() (+9 more)

### Community 88 - "httpx"
Cohesion: 0.17
Nodes (10): httpx, available_models(), Which models a delegated coding session may use. NOT models.py, and…, The model to actually use. Falls back rather than raising: Eve has already told…, validate(), pool(), fixture, The one test in this suite that hits the real, uvicorn-run eve-sandbox service… (+2 more)

### Community 89 - "test_eval_hygiene.py"
Cohesion: 0.14
Nodes (17): Contradictions, _cosine(), find_dead(), find_duplicates(), BaseModel, Cosine similarity. A bare dot product is correct ONLY if both vectors are unit-…, (keeper, loser, score) for each near-identical pair in one scope. Auto-…, Rules whose last_seen_at has not moved inside the window. Report only: a… (+9 more)

### Community 90 - "call_service"
Cohesion: 0.29
Nodes (7): call_service(), get_state(), list_entities(), tool, List the home's entities with their current states, optionally limited to one…, Read the current state of a Home Assistant entity, e.g. 'light.kitchen'., Call a Home Assistant service, e.g. domain='light', service='turn_on'.

### Community 91 - "title.py"
Cohesion: 0.07
Nodes (34): langgraph_constants, is_ambient_text(), _last_write_wins(), True when this message was composed by the ambient pipeline rather than typed…, Last-write-wins, shared by `dynamic_tools` and `suggestions`. A reducer is what…, _aegra_default_title(), clean(), _first_exchange() (+26 more)

### Community 92 - "Handoff: emit `tool_labels` from the LangGraph graph"
Cohesion: 0.17
Nodes (11): Failure, Handoff: emit `tool_labels` from the LangGraph graph, Ordering does not matter, Pointers into the client, Rules the client enforces, Scope, The contract, Verifying it (+3 more)

### Community 93 - "Oura Client Testing"
Cohesion: 0.20
Nodes (15): mock, Oura v2 client and normalizers. The join in `get_recovery` is the thing to keep…, daily_sleep carries the score; the `sleep` collection carries the durations.…, _sleep_record(), test_a_401_refreshes_once_and_retries(), test_a_daily_score_with_no_detailed_row_nulls_the_durations(), test_a_missing_step_count_is_none_not_zero(), test_a_nap_does_not_win_the_join_over_the_nights_sleep() (+7 more)

### Community 94 - "Gmail API Client"
Cohesion: 0.17
Nodes (16): Credentials, email_mime_text, google_auth_transport_requests, google_oauth2_credentials, googleapiclient_discovery, _credentials_for(), _flatten(), get_thread() (+8 more)

### Community 95 - "poller.py"
Cohesion: 0.24
Nodes (9): poll(), Finished computer tasks as signals. The relevance filter is bypassed for this…, _summary(), src_eve_computer_init, datetime, The poller state machine: for every task Eve is still waiting on, ask the box…, Returns the rows that resolved - finished, failed, or went stale - on this…, sync() (+1 more)

### Community 96 - "Sandbox HTTP API"
Cohesion: 0.17
Nodes (15): pydantic_settings, _check_auth(), _gate(), healthz(), invoke(), InvokeBody, BaseModel, get (+7 more)

### Community 97 - "auth.py"
Cohesion: 0.08
Nodes (31): hmac, PyJWKClient, read, allow_assistant_read(), _ambient_subject(), _header(), _jwk_client(), only_own_threads() (+23 more)

### Community 98 - "Oura API Client"
Cohesion: 0.26
Nodes (16): _get(), get_activity(), get_recovery(), get_sleep(), _hours(), _newest_first(), _num(), Oura v2 client. Plain httpx, documented REST. Two things differ from WHOOP and… (+8 more)

### Community 99 - "catalog.py"
Cohesion: 0.13
Nodes (23): _album_asset_list(), album_for(), The sync, and the one text rendering of a wardrobe. `sync` is a batch job. It…, The sync body. `limit` bounds how many photographs one call will describe, so…, `(note, uncatalogued)`. One extra API call, no vision, no measurable latency -…, The whole catalogue as one string, grouped by category., The member's Immich album id, from the roster. `None` when they have no…, `(assets, truncated, error)`. `invoke` returns a JSON string, or a string… (+15 more)

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

### Community 104 - "caldav_client.py"
Cohesion: 0.23
Nodes (11): caldav, icalendar, _as_utc_iso(), _calendars(), _credentials_for(), list_events(), _run(), CalDAV client. One credential per family member, the same shape gmail.py uses:… (+3 more)

### Community 105 - "OAuth Provisioning Server"
Cohesion: 0.17
Nodes (13): http_server, authorize_url(), _await_code(), _exchange(), expires_at(), _main(), datetime, One-time provisioning of a member's WHOOP or Oura credential. uv run python -m… (+5 more)

### Community 106 - "test_acp_client.py"
Cohesion: 0.15
Nodes (19): acp, acp_schema, collections_abc, PathEscapedRoot, Exception, The box's side of the Agent Client Protocol. Deliberately ignorant of HTTP,…, An `fs/*` path resolved outside the session root., _client() (+11 more)

### Community 107 - "_handle"
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

### Community 114 - "test_ambient_integration.py"
Cohesion: 0.17
Nodes (12): langgraph_sdk_errors, _ambient_client(), _member_client(), Ambient's impersonation path against a live `aegra serve`. Requires `docker…, This is the whole point of impersonating rather than pushing only: the member…, Belt and braces on the unit test in Task 9: at the HTTP boundary, the header…, The veto path deletes the thread it just created; it must be allowed to., test_a_member_token_with_the_header_still_authenticates_as_itself() (+4 more)

### Community 115 - "Agent Registry Management"
Cohesion: 0.20
Nodes (12): build(), Exception, Agent name + model in, argv + environment out. Three entries in a dict. No…, An agent name outside AGENT_NAMES., UnknownAgent, fixture, _settings(), test_an_unknown_agent_is_refused() (+4 more)

### Community 116 - "ambient_marker"
Cohesion: 0.18
Nodes (9): ambient_marker(), The guard that matters. Ambient content is untrusted input: a phishing email…, The guard is scoped to authoring. Phase 4 ships fact extraction on ambient…, The guard covers erasure, not just authoring. An ambient turn cannot write a…, test_a_forget_targeting_a_rule_is_refused_on_an_ambient_turn(), ainvoke(), overlapping(), test_a_rule_op_is_refused_on_an_ambient_turn() (+1 more)

### Community 117 - "refresh"
Cohesion: 0.17
Nodes (8): Expired and nothing to refresh with. Must not silently return the dead access…, Concurrent rotation must preserve the one valid refresh token. A real Postgres…, test_a_fresh_token_is_returned_without_refreshing(), refresh(), test_a_rejected_refresh_raises_reconnect_required(), test_an_expired_token_refreshes(), test_an_expired_token_with_no_refresh_token_raises_reconnect_required(), test_two_concurrent_refreshes_rotate_the_token_exactly_once()

### Community 118 - "Memory Graph Execution"
Cohesion: 0.14
Nodes (10): Recall is what makes an opener reflect who is asking. Extract has no exchange…, Recall must inform the answer it precedes; extract must not delay it., test_an_openers_request_still_runs_recall_but_not_extract_or_suggest(), suggest(), test_memory_reaches_the_system_prompt(), test_the_graph_runs_recall_before_eve_and_extract_after(), extract(), recall() (+2 more)

### Community 120 - "Empty Thread Openers"
Cohesion: 0.13
Nodes (15): _empty_state(), A brand new thread: no messages at all, which is exactly what the client's…, Who is asking and when are the only two signals an empty chat has. An opener…, A REFLEX model reading `Noah:` followed by nothing fills the blank in, which…, One switch for both chip flavours: a deployment that turned chips off must not…, An empty canvas waiting on a slow REFLEX call is a blank screen, so the budget…, The empty canvas shows nothing at all when this fails, which is the designed…, The client has ONE `suggestions` handler. Openers arriving under a different… (+7 more)

### Community 121 - "UI Component State"
Cohesion: 0.13
Nodes (15): _create(), `stateKey` names a localState slot to WRITE. A `$data.` binding resolves…, Both would mean one tap with two meanings, and the client would have to pick an…, A button that does nothing renders as a live control that silently ignores taps…, test_a_button_may_not_do_both(), test_a_button_may_not_do_neither(), test_a_button_may_set_local_state(), test_a_button_may_submit() (+7 more)

### Community 122 - "Home Assistant Integration"
Cohesion: 0.22
Nodes (13): respx, fixture, mock, HA sends every attribute of every entity. Forwarding that wholesale would spend…, _settings(), test_call_service_posts_to_home_assistant(), test_get_state_reads_from_home_assistant(), test_get_state_sends_the_bearer_token() (+5 more)

### Community 123 - "embed.py"
Cohesion: 0.14
Nodes (19): OpenAIEmbeddings, embed_query(), embed_texts(), from_pgvector(), get_embedder(), _normalise(), The embedding client. Truncate to the pinned dimension, then RE-NORMALISE.…, pgvector's text input format. ponytail: a string literal cast with `%s::vector`… (+11 more)

### Community 124 - "Health Provider Fan-out"
Cohesion: 0.19
Nodes (10): _clamp_days(), _fan_out(), get_activity(), get_recovery(), get_sleep(), Fan-out across whichever health providers a member has connected. Knows both…, 1..MAX_DAYS. Clamped here rather than trusted: `days` arrives from a model, and…, skipif (+2 more)

### Community 125 - "logging"
Cohesion: 0.11
Nodes (20): langchain_core_exceptions, langchain_core_messages, logging, Scoring one replayed item. Three of the five scorers are exact comparisons and…, Eve writing her own procedures. A rule rides the REFLEX extraction pass…, Copy this turn's surfaces into the AI message, so a card survives a relaunch.…, _coerce_category(), describe() (+12 more)

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

### Community 131 - "test_eval_datasets.py"
Cohesion: 0.18
Nodes (10): A run where the canary passes means the judge is rubber-stamping. This is the…, replay_turn calls the real graph, which resolves `member` through…, A malformed jsonb blob must be skipped, not crash the build., The harness imports Eve; Eve never imports the harness. Otherwise a bug in the…, test_build_ambient_excludes_a_row_whose_signal_will_not_rehydrate(), test_build_ambient_shapes_a_decision_row(), test_build_turns_reads_the_golden_file(), test_every_golden_file_member_resolves_in_the_real_roster() (+2 more)

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

### Community 137 - "test_skills_integration.py"
Cohesion: 0.13
Nodes (12): clean_pool(), fixture, integration, DoD 2: write_skill in one thread, search_skills finds it in another., The phase's core invariant, asserted at the TURN boundary rather than inside…, DoD 3: the superseded_by chain records the revision., DoD 1, 4 and 8: authored rule -> next turn's prompt -> revoked -> gone, with…, test_a_rule_reaches_the_next_turns_prompt_then_is_revoked() (+4 more)

### Community 138 - "Wardrobe Specialist Tests"
Cohesion: 0.15
Nodes (3): The binding contract: a tool returns a string and never raises - a database…, test_read_wardrobe_degrades_when_the_store_raises(), test_the_stylist_reads_the_wardrobe_through_its_loop()

### Community 139 - "Sandbox Security Integration"
Cohesion: 0.15
Nodes (12): pool(), fixture, DoD 7's second half, and the claim §6.3 rests on: with the AST checker bypassed…, DoD 12. The sandbox is the one package that must be unable to reach anything:…, DoD 1, 3: the whole path, with the interrupt resolved by the CLI., DoD 4: the old version keeps serving until the new one is approved., DoD 7's first half: the checker runs again at approval time, so a row edited…, test_a_changed_source_needs_a_fresh_approval() (+4 more)

### Community 140 - "UI Protocol Streaming"
Cohesion: 0.17
Nodes (4): Reasoning-capable models return `content` as a list of typed blocks.…, The server side of `assistant-ui/1.0`, tested against the shapes the client's…, test_append_frame_appends_a_new_block_to_list_content(), test_strip_frames_from_content_drops_the_whole_block_for_list_content()

### Community 141 - "immich.py"
Cohesion: 0.24
Nodes (9): AsyncClient, base64, album_assets(), asset_image(), _client(), Immich REST client. No SDK: two GETs behind a long-lived API key, and a…, Every asset in one album: id and original filename, nothing else. `truncated`…, One asset's preview, base64-encoded. (+1 more)

### Community 142 - "Wardrobe Asset Store"
Cohesion: 0.17
Nodes (3): psycopg_errors, pool(), fixture

### Community 143 - "_read_capped"
Cohesion: 0.40
Nodes (5): Read from `stream` until EOF or until more than `cap` bytes have arrived,…, Like `_read_capped`, but never stops reading once the cap is crossed: it keeps…, _read_capped(), _read_stderr_capped(), StreamReader

### Community 144 - "test_specialists_integration.py"
Cohesion: 0.27
Nodes (9): _ask_how_many_lights(), _counting_model(), Integration test exercising the real HTTP boundary between a specialist and a…, EVE-15 end to end over the real HTTP boundary: six `get_state` rounds used to…, Past the budget the member must still get English, not the name of an exception…, A model that answers "how many lights are on?" the only way `ask_home` allows:…, test_ask_home_reads_real_state_through_a_running_eve_tools(), test_how_many_lights_are_on_survives_a_get_state_per_light() (+1 more)

### Community 145 - "DeliveryError"
Cohesion: 0.27
Nodes (10): DeliveryError, Exception, Infrastructure failed rather than Eve declining. The caller must leave the…, Two members, one delivery raises: the whole signal is deferred, not just the…, The other half of item 1: a retry must not re-deliver, re-push, or re-spend the…, test_a_partial_defer_leaves_the_signal_unseen(), _deliver(), test_a_retry_after_a_partial_defer_only_reaches_the_missed_member() (+2 more)

### Community 146 - "test_computer_dispatch.py"
Cohesion: 0.67
Nodes (6): _config(), _state(), test_a_dispatch_failure_is_returned_and_nothing_is_recorded(), test_a_member_without_the_permission_is_denied(), test_a_permitted_member_dispatches_and_records_the_task(), test_no_thread_id_is_refused_before_dispatching()

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

### Community 153 - "test_sandbox_docker_image.py"
Cohesion: 0.13
Nodes (17): shutil, _docker_available(), fixture, The one test that verifies the *built Docker image* can actually import and run…, Critical 1 regression, at the actual image level. Before the fix, every /invoke…, sandbox_container(), sandbox_image(), test_the_built_image_can_invoke_a_tool() (+9 more)

### Community 154 - "conn"
Cohesion: 0.25
Nodes (8): Phase 2 writes personal memory into the store; scope every operation to the…, scope_store_to_member(), _install(), _spawn(), conn(), fixture, fixture, store()

### Community 155 - "stylist.py"
Cohesion: 0.28
Nodes (12): list_events(), _member(), RunnableConfig, tool, Stylist specialist: what to wear today, from the clothes the member owns. The…, Read the member's whole wardrobe catalogue, grouped by category. Call this…, Today's forecast for the household., What is on the member's calendar for the rest of today. Requires the… (+4 more)

### Community 156 - "UI Component Schema"
Cohesion: 0.27
Nodes (10): _component(), _component_array(), components_schema(), _properties(), _properties_for(), _property(), The catalog, expressed as the `show_surface` argument schema. ADR 0017 put the…, The value constraints `protocol._validate_property` enforces, said in schema… (+2 more)

### Community 157 - "Memory Registry Fixtures"
Cohesion: 0.18
Nodes (4): _clean_pending(), fixture, No background extraction may leak from one test into the next., recorded()

### Community 158 - "Decision"
Cohesion: 0.32
Nodes (6): decide(), Decision, BaseModel, The decision is one call, on the tier that exists for code., test_decide_asks_tier_code_and_gets_a_structured_answer(), ainvoke()

### Community 159 - "MCP Server Registry"
Cohesion: 0.29
Nodes (8): mcp, mcp_client_stdio, invoke(), Generic dispatcher for dynamically-discovered MCP tools. A fresh connection per…, Registered MCP servers, by id. Empty in production until a concrete skill needs…, register(), server_params_for(), StdioServerParameters

### Community 160 - "test_sandbox_app.py"
Cohesion: 0.32
Nodes (5): client(), fixture, _sha(), test_invoke_requires_the_bearer_token(), test_invoke_runs_the_tool()

### Community 161 - "Home Assistant Stub"
Cohesion: 0.25
Nodes (8): fastapi, call_service(), get_state(), list_states(), get, post, A minimal stand-in for Home Assistant's REST API, for integration tests that…, HA returns every entity, not just the lights, and each one carries an…

### Community 162 - "filter.py"
Cohesion: 0.21
Nodes (13): FilterError, _household_context(), judge(), load_filter_prompt(), Exception, The REFLEX-tier relevance gate: is this worth interrupting anyone over?…, The REFLEX call itself could not be completed — a couldn't-decide, not a…, Household memory only. Profile memory is deliberately not read: the audience is… (+5 more)

### Community 164 - "FilterVerdict"
Cohesion: 0.28
Nodes (9): FilterVerdict, BaseModel, Ambient filtering runs on every household signal; it must never spend the…, test_the_reflex_tier_is_the_one_used(), _get_model(), `urgent` bypasses the cap and quiet hours, never the permission gate (fix round…, test_urgent_cannot_bypass_the_permission_gate(), test_replay_ambient_calls_the_real_judge() (+1 more)

### Community 165 - "Model Provider Config"
Cohesion: 0.22
Nodes (8): models, npm, options, apiKey, baseURL, provider, litellm, $schema

### Community 166 - "test_tool_messages_never_reach_the_extraction_prompt"
Cohesion: 0.33
Nodes (3): Currently incidental - last_exchange reads only Human and AI messages. This…, test_tool_messages_never_reach_the_extraction_prompt(), ainvoke()

### Community 167 - "OAuth Token Store"
Cohesion: 0.22
Nodes (5): migrated(), fixture, eve-tools' own pool, against the real Postgres. ADR 0016: eve-tools holds one…, Two members with the same provider must coexist, and one member must not get…, test_the_primary_key_is_provider_plus_member()

### Community 168 - "Dynamic Tool Binding"
Cohesion: 0.22
Nodes (4): test_a_dynamically_bound_tool_is_callable_the_turn_it_is_discovered(), fake_materialize(), test_eve_calls_a_tool_and_returns_the_final_answer(), factory()

### Community 169 - "Detached Span Tracing"
Cohesion: 0.22
Nodes (3): Attributes set on an ended span are silently dropped, and the run's span HAS…, test_a_detached_extraction_records_its_own_span(), ainvoke()

### Community 170 - "pytest"
Cohesion: 0.11
Nodes (10): pytest, mock_server_script(), fixture, Exercises the generic MCP dispatcher against a real local MCP server run over…, content='' -> "".splitlines() == [] -> [0] raises IndexError unless _render…, The row is the audit trail. forget() is a hard DELETE and is the wrong verb…, test_authored_lists_rules_and_procedures(), test_render_handles_empty_content() (+2 more)

### Community 171 - "get_budgets"
Cohesion: 0.40
Nodes (5): get_budgets(), list_transactions(), tool, List recent transactions, optionally filtered by category., Read current budget and cash-flow summary.

### Community 172 - "Health Metric Tools"
Cohesion: 0.32
Nodes (8): get_activity(), get_recovery(), get_sleep(), RunnableConfig, tool, Recovery score, HRV, and resting heart rate for recent days., Sleep duration, stages, and efficiency for recent nights., Training load, calories, steps, and workouts for recent days.

### Community 173 - "Computer Signal Routing"
Cohesion: 0.25
Nodes (7): _computer_signal(), The verdict is synthesised directly from the signal's own member_sub, not…, gates.permitted still runs - a member without computer.use is dropped even…, test_a_computer_signal_is_addressed_only_to_its_own_member(), test_a_computer_signal_never_calls_the_filter(), test_a_computer_signal_records_no_eval_decision(), test_a_computer_signal_still_respects_the_permission_gate()

### Community 174 - "Tool Loop Budget"
Cohesion: 0.25
Nodes (5): LangGraph's own recursion_limit defaults to 10007 and `.compile()` takes no…, The bound is per turn, not per thread: Aegra checkpoints `messages` across…, test_the_loop_budget_resets_on_the_next_turn(), test_the_tool_loop_is_bounded_when_the_model_never_answers(), noop()

### Community 175 - "test_skills_build_a_ui.py"
Cohesion: 0.28
Nodes (8): re, _documented(), The skill's property table is a FIFTH copy of the catalog, and the only one…, Every `- \x60type\x60: prop, prop` line in the skill body., `rank_skills` embeds `description or name`, so an empty description would make…, test_every_documented_property_matches_the_validator(), test_the_skill_documents_every_component_type(), test_the_skill_has_a_description_for_semantic_ranking()

### Community 176 - "Sandbox Child Process"
Cohesion: 0.33
Nodes (6): contextlib, io, resource, _limit(), main(), The child process. Reads one job on stdin, writes one JSON line on stdout.…

### Community 177 - "strip_frames_from_content"
Cohesion: 0.50
Nodes (4): Undo what `persist_ui` did to an AIMessage's content, for every place a…, `strip_frames` operates on a plain string; `AIMessage.content` is sometimes…, strip_frames(), strip_frames_from_content()

### Community 178 - "test_skills_materialize.py"
Cohesion: 0.19
Nodes (8): A tool approved and then used once was a wasted approval. That is only visible…, The result is already computed. Losing a counter must not lose it., test_a_counting_failure_does_not_fail_the_call(), test_a_sandbox_call_counts_the_invocation(), record_invocation(), test_a_sandbox_spec_dispatches_to_the_sandbox(), invoke(), test_materialized_tool_calls_the_mcp_dispatcher()

### Community 180 - "Thread Digest Summarization"
Cohesion: 0.29
Nodes (3): The thread digest summarises the WHOLE transcript, not just the last exchange -…, test_the_digest_transcript_strips_a_persisted_frame(), ainvoke()

### Community 181 - "send_email"
Cohesion: 0.32
Nodes (8): get_thread(), list_messages(), RunnableConfig, tool, Search Gmail. Gmail query syntax, e.g. 'is:unread from:school'., Read a full Gmail thread by id., Send an email. Requires the mail.send permission., send_email()

### Community 184 - "Background Extraction Switch"
Cohesion: 0.33
Nodes (3): The kill switch has to actually switch. With it off the writes must be visible…, test_the_background_flag_off_keeps_extraction_inline(), ainvoke()

### Community 185 - "Reflex Tier Streaming"
Cohesion: 0.33
Nodes (5): Without TAG_NOSTREAM this model's tokens go out on the `messages` channel and…, `get_model` raising - a realistic startup failure, e.g. a missing LiteLLM key -…, test_a_model_factory_failure_yields_no_chips(), test_the_call_is_reflex_tier_and_never_streams(), factory()

### Community 186 - "Database Migration Graph"
Cohesion: 0.40
Nodes (3): alembic_config, alembic_script, The migration graph must have exactly one head. Two branches that each add a…

### Community 187 - "_with_frame"
Cohesion: 0.67
Nodes (3): AIMessage, `model_copy`, not a freshly-built `AIMessage`: `add_messages` replaces by id,…, _with_frame()

### Community 189 - "test_zero_digest_cadence_does_not_break_a_completed_turn"
Cohesion: 0.20
Nodes (5): _no_overlap(), Digest setup runs after streaming and must not be able to fail the turn., test_a_model_failure_does_not_break_the_turn(), test_zero_digest_cadence_does_not_break_a_completed_turn(), ainvoke()

### Community 190 - "Opener Memory Bundling"
Cohesion: 0.40
Nodes (5): _memory(), `_render_memory` narrows the injected bundle to `profile` + `rules` -…, Same narrow bundle as `suggest`, for the same reason: what shapes a plausible…, test_openers_read_profile_and_rules_but_not_household_or_episodic(), test_the_prompt_carries_profile_and_rules_but_not_household_or_episodic()

### Community 191 - "Sync Verification Script"
Cohesion: 0.83
Nodes (3): app_of(), record(), check-sync.sh script

### Community 201 - "Search Window Logic"
Cohesion: 0.67
Nodes (3): allow_assistant_search(), test_the_search_window_starts_now_and_spans_the_horizon(), _search()

### Community 205 - "AST Security Integration"
Cohesion: 0.67
Nodes (3): integration, THE assumption test. §6.3 claims the AST check is not what holds the line; this…, test_source_bypassing_the_ast_checker_still_cannot_reach_the_network()

## Knowledge Gaps
- **38 isolated node(s):** `images-published.sh script`, `Why this exists`, `Rules the client enforces`, `Ordering does not matter`, `Writing the labels` (+33 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1734 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **58 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_settings()` connect `get_settings` to `Memory`, `Tier`, `graph.py`, `permission_denial`, `extract.py`, `SourcePollError`, `Computer Settings Configuration`, `suggest.py`, `get_pool`, `test_coding_supervisor.py`, `Database Migrations`, `tools_client.py`, `filter.py`, `eve_ambient/app.py`, `asyncio`, `Tool Loop Budget`, `invoke`, `test_specialists_base.py`, `eve/settings.py`, `EveState`, `conftest.py`, `httpx`, `title.py`, `poller.py`, `auth.py`, `Suggestion Node Tests`, `embed.py`, `Recall Token Budgets`?**
  _High betweenness centrality (0.057) - this node is a cross-community bridge._
- **Why does `Tier` connect `Tier` to `filter.py`, `FilterVerdict`, `test_wardrobe_catalog.py`, `graph.py`, `test_specialists_base.py`, `title.py`, `extract.py`, `suggest.py`, `test_eval_scorers.py`, `get_settings`, `Reflex Tier Streaming`, `stylist.py`, `logging`, `Decision`, `tools_client.py`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `get_settings()` (e.g. with `conftest.py` and `test_the_tool_loop_is_bounded_when_the_model_never_answers()`) actually correct?**
  _`get_settings()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `Signal` (e.g. with `_handle_in_background()` and `poll_once()`) actually correct?**
  _`Signal` has 36 INFERRED edges - model-reasoned connections that need verification._
- **Are the 49 inferred relationships involving `Family` (e.g. with `oidc()` and `test_a_dev_token_is_still_accepted_alongside_the_pat_path()`) actually correct?**
  _`Family` has 49 INFERRED edges - model-reasoned connections that need verification._
- **What connects `images-published.sh script`, `Why this exists`, `Rules the client enforces` to the rest of the system?**
  _38 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Memory` be split into smaller, more focused modules?**
  _Cohesion score 0.08562367864693446 - nodes in this community are weakly interconnected._