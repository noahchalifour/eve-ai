from datetime import UTC, datetime

import pytest

from eve.family import Member
from eve_ambient import notify
from eve_ambient.types import FilterVerdict, Signal

MEMBER = Member(
    sub="sub-noah", name="Noah", role="adult", timezone="America/Vancouver",
    permissions=frozenset({"calendar.read"}),
)
SIGNAL = Signal(
    source="calendar", key="uid-1:start:x",
    occurred_at=datetime(2026, 8, 23, 22, 0, tzinfo=UTC),
    member_sub="sub-noah", summary="Upcoming: Dentist at 3pm.", payload={"uid": "uid-1"},
)
VERDICT = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="soon")


class FakeThreads:
    def __init__(self):
        self.created, self.deleted = [], []

    async def create(self, metadata=None, **kwargs):
        self.created.append(metadata or {})
        return {"thread_id": "thread-1"}

    async def delete(self, thread_id):
        self.deleted.append(thread_id)


class FakeRuns:
    def __init__(self, final_text="Dentist at 3 — leave by 2:30.", tool_names=(), error=None):
        self.final_text, self.tool_names, self.error = final_text, tool_names, error
        self.inputs = []

    async def wait(self, thread_id, assistant, input=None, **kwargs):
        self.inputs.append(input)
        if self.error:
            raise self.error
        messages = [{"type": "human", "content": "prompt"}]
        if self.tool_names:
            messages.append(
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"name": name, "args": {}} for name in self.tool_names],
                }
            )
        messages.append({"type": "ai", "content": self.final_text})
        return {"messages": messages}


class FakeClient:
    def __init__(self, runs=None):
        self.threads, self.runs = FakeThreads(), runs or FakeRuns()


class RecordingNotifier:
    def __init__(self, result=True):
        self.result, self.calls = result, []

    async def send(self, *, title, body, urgent, click_url):
        self.calls.append({"title": title, "body": body, "urgent": urgent, "click_url": click_url})
        return self.result


@pytest.fixture(autouse=True)
def ambient_settings(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", "a" * 40)
    monkeypatch.setenv("EVE_AMBIENT_AEGRA_BASE_URL", "http://eve.test:2026")
    monkeypatch.setenv("EVE_AMBIENT_THREAD_URL_TEMPLATE", "https://eve.test/t/{thread_id}")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _with_client(monkeypatch, client):
    monkeypatch.setattr(notify, "get_client", lambda **kwargs: client)
    return client


async def test_a_notification_creates_a_thread_and_pushes(monkeypatch):
    client = _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    thread_id = await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert thread_id == "thread-1"
    assert notifier.calls[0]["body"] == "Dentist at 3 — leave by 2:30."


async def test_the_client_impersonates_the_member(monkeypatch):
    """Aegra scopes threads to the authenticated identity, so this header is
    the whole reason the member can reply in the thread."""
    captured = {}

    def _get_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(notify, "get_client", _get_client)
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert captured["url"] == "http://eve.test:2026"
    assert captured["headers"]["x-eve-on-behalf-of"] == "sub-noah"
    assert captured["headers"]["Authorization"] == f"Bearer {'a' * 40}"


async def test_the_input_is_one_marked_human_message(monkeypatch):
    """recall.py and extract.py both key off the last HumanMessage, so the
    ambient prompt has to be one (design section 6.2)."""
    client = _with_client(monkeypatch, FakeClient())
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    messages = client.runs.inputs[0]["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "[ambient signal" in messages[0]["content"]
    assert SIGNAL.summary in messages[0]["content"]
    assert notify.VETO in messages[0]["content"]


async def test_the_thread_is_tagged_as_ambient(monkeypatch):
    client = _with_client(monkeypatch, FakeClient())
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert client.threads.created[0]["ambient"] is True
    assert client.threads.created[0]["source"] == "calendar"


async def test_a_veto_deletes_the_thread_and_sends_nothing(monkeypatch):
    client = _with_client(monkeypatch, FakeClient(runs=FakeRuns(final_text="NOTHING")))
    notifier = RecordingNotifier()
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier) is None
    assert client.threads.deleted == ["thread-1"]
    assert notifier.calls == []


async def test_an_empty_answer_is_treated_as_a_veto(monkeypatch):
    client = _with_client(monkeypatch, FakeClient(runs=FakeRuns(final_text="   ")))
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier()) is None
    assert client.threads.deleted == ["thread-1"]


async def test_a_failed_run_raises_delivery_error_and_cleans_up(monkeypatch):
    """Aegra being unreachable must leave the signal unseen so the next poll
    retries it (design 6.4), which is what DeliveryError signals."""
    client = _with_client(
        monkeypatch, FakeClient(runs=FakeRuns(error=RuntimeError("aegra down")))
    )
    with pytest.raises(notify.DeliveryError):
        await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert client.threads.deleted == ["thread-1"]


async def test_a_failed_push_still_returns_the_thread(monkeypatch):
    """The content is already in the thread. Retrying would re-run a paid
    turn to re-send text the member can already read."""
    client = _with_client(monkeypatch, FakeClient())
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier(result=False)) == "thread-1"


async def test_an_urgent_verdict_is_passed_to_the_notifier(monkeypatch):
    _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    urgent = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="leak")
    await notify.deliver(SIGNAL, MEMBER, urgent, notifier)
    assert notifier.calls[0]["urgent"] is True
    assert "urgent" in notifier.calls[0]["title"].lower()


async def test_the_click_url_comes_from_the_template(monkeypatch):
    _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert notifier.calls[0]["click_url"] == "https://eve.test/t/thread-1"


async def test_tool_calls_made_during_the_run_are_logged(monkeypatch, caplog):
    """Ambient turns may act (design section 7). Initiative without an audit
    trail is how "why did the garage close" becomes unanswerable."""
    _with_client(
        monkeypatch, FakeClient(runs=FakeRuns(tool_names=("ask_home", "search_memory")))
    )
    with caplog.at_level("INFO"):
        await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    logged = " ".join(record.message for record in caplog.records)
    assert "ask_home" in logged
    assert "search_memory" in logged
    assert SIGNAL.key in logged


async def test_block_style_content_is_flattened(monkeypatch):
    """The Responses API returns content as a list of blocks, not a string."""
    runs = FakeRuns()
    runs.final_text = [{"type": "text", "text": "Leave by 2:30."}]
    _with_client(monkeypatch, FakeClient(runs=runs))
    notifier = RecordingNotifier()
    await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert notifier.calls[0]["body"] == "Leave by 2:30."
