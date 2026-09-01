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
    def __init__(self, fail_delete=False):
        self.created, self.deleted = [], []
        self.fail_delete = fail_delete

    # `metadata` is keyword-only on the real SDK's ThreadsClient.create; a
    # positional-friendly fake would not catch a call-convention regression.
    async def create(self, *, metadata=None, **kwargs):
        self.created.append(metadata or {})
        return {"thread_id": "thread-1"}

    async def delete(self, thread_id):
        if self.fail_delete:
            raise RuntimeError("aegra unreachable")
        self.deleted.append(thread_id)


class FakeRuns:
    def __init__(self, final_text="Dentist at 3 — leave by 2:30.", tool_names=(), error=None):
        self.final_text, self.tool_names, self.error = final_text, tool_names, error
        self.inputs = []
        self.assistant_ids = []

    # `assistant_id` is the real SDK's second positional/keyword parameter;
    # recording it lets a test pin the value deliver() actually sends.
    async def wait(self, thread_id, assistant_id, *, input=None, **kwargs):
        self.inputs.append(input)
        self.assistant_ids.append(assistant_id)
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
        # `final_text=None` models a truncated/interrupted run: no final
        # assistant message follows the tool call (or none at all).
        if self.final_text is not None:
            messages.append({"type": "ai", "content": self.final_text})
        return {"messages": messages}


class FakeClient:
    def __init__(self, runs=None, threads=None):
        self.threads = threads or FakeThreads()
        self.runs = runs or FakeRuns()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class RecordingNotifier:
    def __init__(self, result=True):
        self.result, self.calls = result, []

    async def send(self, *, title, body, urgent, click_url):
        self.calls.append({"title": title, "body": body, "urgent": urgent, "click_url": click_url})
        return self.result


class RaisingNotifier:
    async def send(self, *, title, body, urgent, click_url):
        raise RuntimeError("ntfy client blew up")


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


async def test_the_run_targets_the_eve_assistant(monkeypatch):
    """The reviewer checked aegra.json and it matches today, but nothing
    would notice if the assistant id ever drifted without this pin."""
    client = _with_client(monkeypatch, FakeClient())
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert client.runs.assistant_ids == ["eve"]


async def test_the_thread_is_tagged_as_ambient(monkeypatch):
    client = _with_client(monkeypatch, FakeClient())
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert client.threads.created[0]["ambient"] is True
    assert client.threads.created[0]["source"] == "calendar"
    assert client.threads.created[0]["signal_key"] == SIGNAL.key


async def test_the_payload_is_truncated_in_the_prompt():
    """Pins the truncation: an unbounded payload must not blow up the
    prompt (or the model's context) just because a source attached one."""
    huge_signal = Signal(
        source="calendar", key="uid-huge", occurred_at=SIGNAL.occurred_at,
        member_sub="sub-noah", summary="s", payload={"data": "x" * 5000},
    )
    prompt = notify.compose_prompt(huge_signal, MEMBER, VERDICT)
    detail_line = next(line for line in prompt.splitlines() if line.startswith("Detail: "))
    assert len(detail_line) - len("Detail: ") <= notify._PAYLOAD_CHARS


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


@pytest.mark.parametrize(
    "answer",
    ["NOTHING", "nothing", "Nothing", "Nothing.", "nothing!", "NOTHING?", "  nothing  "],
)
async def test_veto_variants_are_all_treated_as_silence(monkeypatch, answer):
    """The prompt asks Eve to reply with exactly NOTHING, but instruction-
    following on casing is not something to bet a user-visible push on, and
    a bare "nothing" -- with or without a trailing full stop -- is never a
    message worth interrupting someone with."""
    client = _with_client(monkeypatch, FakeClient(runs=FakeRuns(final_text=answer)))
    notifier = RecordingNotifier()
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier) is None
    assert client.threads.deleted == ["thread-1"]
    assert notifier.calls == []


async def test_a_sentence_that_merely_contains_the_word_is_still_delivered(monkeypatch):
    """The failure mode an over-eager veto match would introduce is worse
    than the case-sensitivity bug it fixes: a real answer that happens to
    start with the word "nothing" must still reach the family."""
    text = "Nothing on the calendar until Thursday, but the garage is open."
    client = _with_client(monkeypatch, FakeClient(runs=FakeRuns(final_text=text)))
    notifier = RecordingNotifier()
    thread_id = await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert thread_id == "thread-1"
    assert notifier.calls[0]["body"] == text


async def test_a_failing_discard_does_not_prevent_the_veto_result(monkeypatch):
    """`_discard`'s except is the module's safety net for an unreachable
    Aegra on cleanup; it must swallow, not propagate, or a delete failure
    would turn Eve's silence into a crash."""
    client = FakeClient(runs=FakeRuns(final_text=notify.VETO), threads=FakeThreads(fail_delete=True))
    monkeypatch.setattr(notify, "get_client", lambda **kwargs: client)
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier()) is None


async def test_a_run_with_no_assistant_message_raises_delivery_error(monkeypatch):
    """No assistant message at all (state["messages"] never got an answer)
    is infrastructure failing, not Eve choosing silence: the signal must
    stay unseen so the next poll retries it, not be resolved forever."""
    client = _with_client(
        monkeypatch, FakeClient(runs=FakeRuns(final_text=None, tool_names=()))
    )
    with pytest.raises(notify.DeliveryError):
        await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert client.threads.deleted == ["thread-1"]


async def test_a_truncated_tool_call_run_raises_delivery_error(monkeypatch):
    """A run whose last message carries tool_calls with no later text is a
    truncated/interrupted turn, not a veto — deleting the guard that used to
    check `not message.get("tool_calls")` would silently fold this into
    Eve's silence and permanently resolve a signal nothing actually answered."""
    client = _with_client(
        monkeypatch,
        FakeClient(runs=FakeRuns(final_text=None, tool_names=("ask_home",))),
    )
    with pytest.raises(notify.DeliveryError):
        await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
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


async def test_a_failed_run_logs_before_discarding(monkeypatch, caplog):
    """The audit line must name the thread before `_discard` deletes it —
    otherwise the only trace of what Eve may have already done vanishes with
    the exception."""
    client = _with_client(
        monkeypatch, FakeClient(runs=FakeRuns(error=RuntimeError("aegra down")))
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(notify.DeliveryError):
            await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    logged = " ".join(record.message for record in caplog.records)
    assert MEMBER.sub in logged
    assert SIGNAL.key in logged
    assert "thread-1" in logged


async def test_a_failed_push_still_returns_the_thread(monkeypatch):
    """The content is already in the thread. Retrying would re-run a paid
    turn to re-send text the member can already read."""
    client = _with_client(monkeypatch, FakeClient())
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier(result=False)) == "thread-1"


async def test_a_raising_notifier_still_returns_the_thread(monkeypatch):
    """The Notifier protocol promises never to raise, but nothing at runtime
    enforces that; deliver() must not let a rogue implementation orphan the
    thread or escape as an unhandled exception."""
    client = _with_client(monkeypatch, FakeClient())
    thread_id = await notify.deliver(SIGNAL, MEMBER, VERDICT, RaisingNotifier())
    assert thread_id == "thread-1"


async def test_an_urgent_verdict_is_passed_to_the_notifier(monkeypatch):
    _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    urgent = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="leak")
    await notify.deliver(SIGNAL, MEMBER, urgent, notifier)
    assert notifier.calls[0]["urgent"] is True
    assert "urgent" in notifier.calls[0]["title"].lower()
    # The literal title, ASCII hyphen only: an em dash here would come back
    # from ntfy's header-safe `_ascii()` as "Eve ? urgent" on every single
    # urgent push, the highest-stakes notification the system sends.
    assert notifier.calls[0]["title"] == "Eve - urgent"


async def test_the_click_url_comes_from_the_template(monkeypatch):
    _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert notifier.calls[0]["click_url"] == "https://eve.test/t/thread-1"


async def test_a_malformed_thread_url_template_disables_the_click_url(monkeypatch):
    """Wrong placeholder or a stray brace must not escape deliver() as a
    bare exception after the paid turn: it would skip mark_seen and
    crash-loop the poller on every cycle."""
    monkeypatch.setenv("EVE_AMBIENT_THREAD_URL_TEMPLATE", "https://eve.test/t/{oops}")
    from eve.settings import get_settings

    get_settings.cache_clear()
    _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    thread_id = await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert thread_id == "thread-1"
    assert notifier.calls[0]["click_url"] is None


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


async def test_a_passed_thread_id_is_reused_instead_of_creating_one(monkeypatch):
    threads = FakeThreads()
    client = _with_client(monkeypatch, FakeClient(threads=threads))

    thread_id = await notify.deliver(
        SIGNAL, MEMBER, VERDICT, RecordingNotifier(), thread_id="existing-thread"
    )

    assert thread_id == "existing-thread"
    assert threads.created == []
    assert client.runs.inputs  # the turn still ran


async def test_a_reused_thread_is_never_discarded_on_veto(monkeypatch):
    threads = FakeThreads()
    _with_client(monkeypatch, FakeClient(threads=threads, runs=FakeRuns(final_text="NOTHING")))

    result = await notify.deliver(
        SIGNAL, MEMBER, VERDICT, RecordingNotifier(), thread_id="existing-thread"
    )

    assert result is None
    assert threads.deleted == []


async def test_a_reused_thread_is_never_discarded_on_run_failure(monkeypatch):
    threads = FakeThreads()
    _with_client(
        monkeypatch,
        FakeClient(threads=threads, runs=FakeRuns(error=RuntimeError("aegra down"))),
    )

    with pytest.raises(notify.DeliveryError):
        await notify.deliver(
            SIGNAL, MEMBER, VERDICT, RecordingNotifier(), thread_id="existing-thread"
        )

    assert threads.deleted == []


async def test_without_a_thread_id_behaviour_is_unchanged(monkeypatch):
    """Every existing caller (calendar, mail, finances, home) passes no
    thread_id and must keep creating a fresh one."""
    threads = FakeThreads()
    _with_client(monkeypatch, FakeClient(threads=threads))

    thread_id = await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())

    assert thread_id == "thread-1"
    assert threads.created == [{"ambient": True, "source": "calendar", "signal_key": "uid-1:start:x"}]


def test_compose_prompt_uses_the_shared_marker():
    """notify.py must not hand-roll the prefix: the extract guard matches on
    the constant, so a divergence here disables authoring protection."""
    from eve.state import is_ambient_text

    signal = Signal(
        source="mail", key="k1", occurred_at=datetime(2026, 8, 27, tzinfo=UTC),
        member_sub="sub-noah", summary="A package shipped.",
    )
    member = Member(
        sub="sub-noah", name="Noah", role="adult",
        timezone="America/Toronto", permissions=frozenset(),
    )
    prompt = notify.compose_prompt(signal, member, FilterVerdict(notify=True, why="w"))

    assert is_ambient_text(prompt)
