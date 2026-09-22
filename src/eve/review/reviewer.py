"""Which agent and model review a pull request.

THE ONE RULE IN THIS FILE. The reviewer is never the implementer. EVE-27
asks for a different provider or model than the one that wrote the code, and
the reason is blind spots: a model reviewing its own output shares every
assumption that produced the bug. Falling back to the implementer when the
configured default happens to match it would lose exactly the property the
feature exists for.

Pure and deterministic, with no I/O and no model call. Two reviews of the
same pull request must not differ because the reviewer was drawn at random,
or the member cannot tell a model regression from a coin flip.
"""

from __future__ import annotations

from eve.settings import get_settings

# Ordered by how much this repository trusts them to review rather than
# write, which is not the same ranking as the coding fallback: a reviewer is
# read-only, so a strong reasoner with weak tool use is fine here and would
# be a poor coding agent.
_PREFERENCE: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic/claude-sonnet-5"),
    ("codex", "chatgpt/gpt-5.6-sol"),
    ("dsh", "chatgpt/gpt-5.6-sol"),
)


def choose(implemented_by: tuple[str, str] | None) -> tuple[str, str]:
    """`(agent, model)` for the review.

    `implemented_by` is the pair recorded on the coding session that opened
    the pull request, or `None` for a human-authored one, where there is
    nothing to differ from.
    """
    settings = get_settings()
    default = (settings.review_default_agent, settings.review_default_model)

    if implemented_by is None:
        return default

    implementer_agent, implementer_model = implemented_by
    candidates = (default, *_PREFERENCE)
    for agent, model in candidates:
        if agent != implementer_agent and model != implementer_model:
            return agent, model

    # Every candidate collides, which means the preference table has been
    # narrowed to one entry. Returning the implementer would silently drop
    # the feature's whole premise, so take the first candidate differing on
    # agent alone and accept the weaker guarantee.
    for agent, model in candidates:
        if agent != implementer_agent:
            return agent, model
    return default
