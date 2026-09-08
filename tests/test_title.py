from langchain_core.messages import AIMessage, HumanMessage

from eve import title as title_mod


class FakeModel:
    def __init__(self, result):
        self.result = result
        self.schema = None
        self.tags = None
        self.prompt = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def with_config(self, *, tags):
        self.tags = tags
        return self

    async def ainvoke(self, prompt):
        self.prompt = prompt
        return self.result


def _state(human="Plan dinner", ai="I'll suggest three quick options."):
    return {"messages": [HumanMessage(human), AIMessage(ai)]}


def _config(thread_id="t1"):
    return {"configurable": {"thread_id": thread_id}}


def test_clean_normalizes_whitespace_and_rejects_invalid_titles():
    assert title_mod.clean("  Dinner   planning \n") == "Dinner planning"
    assert title_mod.clean("") is None
    assert title_mod.clean("x" * (title_mod.MAX_CHARS + 1)) is None


async def test_generate_writes_a_reflex_title(monkeypatch):
    model = FakeModel(title_mod.Title(title="Dinner planning"))
    monkeypatch.setattr(title_mod, "get_model", lambda _tier: model)
    written = []

    async def get_thread(_thread_id):
        return {"metadata": {"thread_name": ""}}

    async def update_thread(thread_id, title):
        written.append((thread_id, title))

    await title_mod.generate(
        _state(), _config(), get_thread=get_thread, update_thread=update_thread
    )

    assert written == [("t1", "Dinner planning")]
    assert model.schema is title_mod.Title
    assert model.tags == [title_mod.TAG_NOSTREAM]
    assert "Plan dinner" in str(model.prompt)


async def test_generate_replaces_aegra_first_message_fallback(monkeypatch):
    monkeypatch.setattr(title_mod, "get_model", lambda _tier: FakeModel(title_mod.Title(title="Dinner planning")))
    written = []

    async def get_thread(_thread_id):
        return {"metadata": {"thread_name": "Plan dinner"}}

    async def update_thread(thread_id, title):
        written.append((thread_id, title))

    await title_mod.generate(
        _state(), _config(), get_thread=get_thread, update_thread=update_thread
    )

    assert written == [("t1", "Dinner planning")]


async def test_generate_skips_titled_or_ambient_threads(monkeypatch):
    called = False

    async def get_thread(_thread_id):
        return {"metadata": {"thread_name": "Already titled"}}

    async def update_thread(_thread_id, _title):
        nonlocal called
        called = True

    await title_mod.generate(
        _state(), _config(), get_thread=get_thread, update_thread=update_thread
    )
    await title_mod.generate(
        _state("[ambient signal — not spoken by Noah]\nDoor opened"),
        _config(),
        get_thread=get_thread,
        update_thread=update_thread,
    )

    assert called is False


async def test_title_runs_generation(monkeypatch):
    called = False

    async def fake_generate(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(title_mod, "generate", fake_generate)

    assert await title_mod.title(_state(), _config()) == {}
    assert called is True


async def test_generate_swallows_model_and_update_failures(monkeypatch):
    async def get_thread(_thread_id):
        return {"metadata": {}}

    async def update_thread(_thread_id, _title):
        raise RuntimeError("database down")

    monkeypatch.setattr(title_mod, "get_model", lambda _tier: FakeModel(title_mod.Title(title="Topic")))
    await title_mod.generate(
        _state(), _config(), get_thread=get_thread, update_thread=update_thread
    )
