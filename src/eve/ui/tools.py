"""The one tool the model gets: put the weather card on screen.

No arguments, no forecast from the model, no JSON from the model. The model's
only decision is WHETHER a card is the right answer; everything in it comes
from Home Assistant through `eve.ui.weather`. That asymmetry is the point -
see ADR 0013.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.state import EveState
from eve.tools_client import invoke
from eve.ui import stream, weather

_NO_CLIENT_SUPPORT = (
    "This member's app cannot render weather cards. Answer in words instead."
)
_NO_DATA = (
    "Home Assistant did not return the weather, so there is no card to show. "
    "Say so plainly."
)
_REJECTED = (
    "The weather card was rejected before it could be shown. Answer in words "
    "instead."
)


@tool(response_format="content_and_artifact")
async def show_weather(
    state: Annotated[EveState, InjectedState], config: RunnableConfig
) -> tuple[str, dict | None]:
    """Show the family home's live weather card: current conditions plus an
    hourly strip the member can tap to switch to a 7-day forecast.

    Call this INSTEAD of describing the weather in words when a member asks
    about the weather at home. It takes no arguments and needs no forecast
    from you - it reads Home Assistant itself. Do not call it for another
    city: it only knows the home's own weather.
    """
    if not stream.supports(config, weather.CATALOG_ID):
        return (_NO_CLIENT_SUPPORT, None)

    forecast = weather.decode_forecast(await invoke("home.weather", {}))
    if forecast is None:
        return (_NO_DATA, None)

    # `load_context` always stamps `member` before any tool can run, so this
    # is a total lookup in the graph. The fallback is for a direct invoke.
    timezone = (state or {}).get("member", {}).get("timezone") or "UTC"
    operation = weather.build_create(weather.new_surface_id(), forecast, timezone)
    if not stream.emit(operation):
        return (_REJECTED, None)
    return (weather.summary(forecast), operation)
