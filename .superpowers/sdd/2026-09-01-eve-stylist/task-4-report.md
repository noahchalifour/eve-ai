# Task 4 report: wardrobe vision pass

## Result

Implemented the Task 4 wardrobe vision pass exactly against the brief. One
base64 image preview is sent in one REFLEX-tier structured-output call and is
converted into `WardrobeItem` records; pixels do not enter EveState or a
specialist loop.

## TDD evidence

### RED

Command:

```text
uv run pytest tests/test_wardrobe_vision.py -v
```

Result: collection failed as expected with:

```text
ImportError: cannot import name 'vision' from 'eve.wardrobe'
5 tests collected: 0 items / 1 error
```

### GREEN

Command:

```text
uv run pytest tests/test_wardrobe_vision.py -v
```

Result:

```text
5 passed in 0.02s
```

## Verification

Focused suite:

```text
uv run pytest tests/test_wardrobe_vision.py -v
5 passed in 0.02s
```

Full default unit suite:

```text
uv run pytest -q
795 passed, 173 deselected, 8 errors in 18.35s
```

The eight errors are unrelated existing `eve_computer` setup failures caused
by `ModuleNotFoundError: No module named 'claude_agent_sdk'`. No unrelated
code was changed.

Lint/diff checks:

```text
uv run ruff check src/eve/wardrobe/vision.py tests/test_wardrobe_vision.py
error: Failed to initialize cache at `/Users/nchalifo/.cache/uv`
      Operation not permitted (os error 1)
```

The repository-local `.venv/bin/ruff` was unavailable. `git diff --check`
completed with no output, indicating no whitespace errors.

## Files

- `prompts/wardrobe.md` — exact brief prompt.
- `src/eve/wardrobe/vision.py` — schemas, defaults/ranges, prompt loading,
  REFLEX call, data URL construction, category coercion, and row conversion.
- `tests/test_wardrobe_vision.py` — the five specified tests.

## Self-review

- Uses `get_model(Tier.REFLEX)` and does not hardcode a model identifier.
- Keeps image data local to `describe`; no state, store, or specialist changes.
- Uses the exact six-category tuple and coerces unknown categories to
  `accessory`.
- Uses the requested `data:{content_type};base64,{image_base64}` shape.
- Uses Pydantic defaults and inclusive 1–5 bounds from the brief.
- Includes the requested ponytail comments around the deliberate tier and
  structured schema choices.

## Concerns

The full suite cannot be completely clean in this environment until the
optional `claude-agent-sdk` dependency is available. Focused Task 4 tests are
fully green; no Task 4-specific concern remains.

## Commit

Committed as:

```text
feat(wardrobe): describe a garment photo on the REFLEX tier
```

## Fix round 1

Review finding addressed: added a nearby ponytail comment explicitly marking
the one-vision-call-per-photograph ceiling and naming retries or batching as
the upgrade path if transient failures or throughput become requirements.
The specified interfaces, REFLEX tier, and relative prompt path were not
changed.

Exact verification command and output:

```text
uv run pytest tests/test_wardrobe_vision.py -v
============================== 5 passed in 0.02s ===============================
```

`git diff --check` completed with no output.
