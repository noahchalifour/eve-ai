"""Create the thread, run Eve on it, and push the result.

This module knows Aegra and ntfy. It does not know why it was called: the
gates ran before it, and the decision to spend a VOICE-tier turn has already
been made.
"""

from __future__ import annotations

import json
import logging

from langgraph_sdk import get_client

from eve.family import Member
from eve.settings import get_settings
from eve_ambient.ntfy import Notifier
from eve_ambient.types import FilterVerdict, Signal

logger = logging.getLogger(__name__)

VETO = "NOTHING"
_PAYLOAD_CHARS = 800
_ASSISTANT = "eve"


class DeliveryError(Exception):
    """Infrastructure failed rather than Eve declining. The caller must leave
    the signal unseen so the next poll retries it."""


def _client(member_sub: str):
    settings = get_settings()
    return get_client(
        url=settings.ambient_aegra_base_url,
        headers={
            "Authorization": f"Bearer {settings.ambient_token}",
            "x-eve-on-behalf-of": member_sub,
        },
    )


def compose_prompt(signal: Signal, member: Member, verdict: FilterVerdict) -> str:
    """A marked human message, not a developer one: recall.py and extract.py
    both key off the last HumanMessage, so a developer message would silently
    cost this turn its episodic recall and half its extraction (design 6.2).
    The marker also tells Eve she was not spoken to, and leaves the thread
    showing what prompted her.
    """
    return (
        f"[ambient signal — not spoken by {member.name}]\n"
        f"{signal.summary}\n"
        f"Source: {signal.source}. Noticed at {signal.occurred_at.isoformat()}.\n"
        f"Detail: {json.dumps(signal.payload, default=str)[:_PAYLOAD_CHARS]}\n"
        f"Why this reached you: {verdict.why}\n\n"
        f"You noticed this; nobody asked you. Decide whether {member.name} needs "
        f"to know right now. If it is worth saying, say it in one or two "
        f"sentences in your own voice, and act only if acting is plainly what "
        f"they would want. If it is not worth saying, reply with exactly "
        f"{VETO} and nothing else."
    )


def _text_of(content) -> str:
    """Content is a string on the Chat Completions path and a list of blocks
    on the Responses path."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _final_text(state: dict) -> str | None:
    """`None` means no final assistant message was found at all — the run
    was truncated, hit a recursion limit, or ended mid-tool-loop with no
    later answer. That is infrastructure failing and must not be mistaken
    for Eve's veto, which is a real (possibly empty or `NOTHING`) message
    text. Callers distinguish the two: `None` -> `DeliveryError`, an actual
    (even empty) string -> the veto/answer check."""
    for message in reversed(state.get("messages") or []):
        role = message.get("type") or message.get("role")
        if role in ("ai", "assistant") and not message.get("tool_calls"):
            return _text_of(message.get("content")).strip()
    return None


def _tools_called(state: dict) -> list[str]:
    names = []
    for message in state.get("messages") or []:
        for call in message.get("tool_calls") or []:
            name = call.get("name")
            if name:
                names.append(name)
    return names


def _is_veto(text: str) -> bool:
    """Case-insensitive, and tolerant of a trailing full stop/exclamation/
    question mark (`Nothing.`, `NOTHING!`): instruction-following on exact
    casing is not something to bet a user-visible push on, and a bare
    "nothing" is never worth interrupting someone with regardless of case or
    a trailing period. Only *trailing* punctuation is stripped and this
    remains an equality check, not substring matching, so a real sentence
    that merely contains the word ("Nothing on the calendar until
    Thursday...") still fails the match and gets delivered.
    """
    return not text or text.rstrip(".!?").upper() == VETO


def _click_url(thread_id: str) -> str | None:
    template = get_settings().ambient_thread_url_template
    if not template:
        return None
    try:
        return template.format(thread_id=thread_id)
    except (KeyError, IndexError, ValueError):
        # A misconfigured EVE_AMBIENT_THREAD_URL_TEMPLATE (wrong placeholder,
        # a stray brace) must not escape deliver(): this runs after the paid
        # turn, and an unguarded exception here would propagate as neither a
        # thread id nor a DeliveryError, skip mark_seen entirely, and
        # crash-loop the poller. Drop the click URL instead.
        logger.warning(
            "EVE_AMBIENT_THREAD_URL_TEMPLATE is malformed: %r", template, exc_info=True
        )
        return None


async def deliver(
    signal: Signal, member: Member, verdict: FilterVerdict, notifier: Notifier
) -> str | None:
    async with _client(member.sub) as client:
        try:
            thread = await client.threads.create(
                metadata={
                    "ambient": True,
                    "source": signal.source,
                    "signal_key": signal.key,
                }
            )
            thread_id = thread["thread_id"]
        except Exception as exc:
            raise DeliveryError(f"could not create a thread: {exc}") from exc

        try:
            state = await client.runs.wait(
                thread_id,
                _ASSISTANT,
                input={
                    "messages": [
                        {"role": "user", "content": compose_prompt(signal, member, verdict)}
                    ]
                },
            )
        except Exception as exc:
            # Logged before discard: an ambient turn carries Eve's full
            # toolset, so she may have already acted before Aegra failed,
            # and the thread about to be deleted is the only trace of that.
            logger.warning(
                "ambient run failed member=%s key=%s thread=%s",
                member.sub, signal.key, thread_id, exc_info=True,
            )
            await _discard(client, thread_id)
            raise DeliveryError(f"the compose turn failed: {exc}") from exc

        tools = _tools_called(state)
        logger.info(
            "ambient turn member=%s source=%s key=%s thread=%s tools=%s",
            member.sub, signal.source, signal.key, thread_id, ",".join(tools) or "none",
        )

        text = _final_text(state)
        if text is None:
            # No final assistant message at all: truncated, recursion-limited,
            # or interrupted mid-tool-loop. This is infrastructure failing,
            # not Eve choosing silence, so the signal must stay unseen for
            # the next poll to retry (design 6.4) rather than being resolved
            # by a turn that produced nothing.
            logger.warning(
                "ambient run produced no final answer member=%s key=%s thread=%s",
                member.sub, signal.key, thread_id,
            )
            await _discard(client, thread_id)
            raise DeliveryError("the compose turn produced no final answer")

        if _is_veto(text):
            logger.info("Eve declined to speak about %s; discarding the thread", signal.key)
            await _discard(client, thread_id)
            return None

        title = "Eve - urgent" if verdict.urgent else "Eve"
        try:
            sent = await notifier.send(
                title=title, body=text, urgent=verdict.urgent, click_url=_click_url(thread_id)
            )
        except Exception:
            # The Notifier protocol promises not to raise; structural typing
            # does not enforce that at runtime. A raise here has the same
            # orphaned-thread, escape-past-Task-12 shape as a malformed
            # click URL, so it is treated identically to `sent is False`.
            logger.warning("the push raised for %s", thread_id, exc_info=True)
            sent = False
        if not sent:
            # Deliberately still a success: the message is in the thread the
            # member owns. Retrying would re-run a paid turn to re-send text
            # they can already read.
            logger.warning("the push failed but %s holds the message", thread_id)
        return thread_id


async def _discard(client, thread_id: str) -> None:
    try:
        await client.threads.delete(thread_id)
    except Exception:
        logger.warning("could not delete the ambient thread %s", thread_id, exc_info=True)
