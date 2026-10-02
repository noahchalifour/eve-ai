"""`FAST_ACTIONS`: the only calls that may ever become a shortcut.

Closed and code-defined. A call outside this table is never recorded and
never promoted, whatever a specialist did. Every entry is a read or a
one-tap (`safe`) action; the risk classes come from the widget action
registry (`eve.widgets.actions.home.TOGGLE_RISK`), so the house has one
answer to "is this one tap or does it ask first".

Each entry knows four things about one specialist tool:
    which argument varies (collapsed into one shortcut's options),
    how to check a concrete call is still allowed,
    which permission it needs,
    and how to make the call directly against eve-tools.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from eve.tools_client import invoke
from eve.widgets.actions.home import TOGGLE_RISK

# A specialist's look-ups that lead to the real call. Stripped from a trace
# before fingerprinting: skipping them is where the speed comes from.
DISCOVERY = frozenset({"list_entities", "search_skills"})

SAFE_HOME_DOMAINS = frozenset(
    {domain for domain, risk in TOGGLE_RISK.items() if risk == "safe"} | {"scene", "media_player"}
)
SAFE_HOME_SERVICES = frozenset({
    "turn_on", "turn_off", "toggle",
    "media_play_pause", "media_next_track", "media_previous_track",
    "volume_up", "volume_down",
})
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAX_HEALTH_DAYS = 14


@dataclass(frozen=True)
class FastAction:
    specialist: str
    tool: str
    permission: str
    # The argument whose observed values become one shortcut's options, or
    # None when every argument is fixed.
    variant_key: str | None
    write: bool
    check: Callable[[dict], str | None]
    run: Callable[[dict, dict], Awaitable[str]]
    name: Callable[[dict], str]
    describe: Callable[[dict, list[str]], str]


def _entity_ok(args: dict) -> str | None:
    entity = args.get("entity_id")
    if not isinstance(entity, str) or not _ENTITY.match(entity):
        return "not a single entity id"
    return None


def _check_call_service(args: dict) -> str | None:
    if (err := _entity_ok(args)) is not None:
        return err
    domain = args.get("domain")
    if domain not in SAFE_HOME_DOMAINS:
        return f"{domain!r} is not a one-tap domain"
    if not str(args["entity_id"]).startswith(f"{domain}."):
        return "domain does not match the entity"
    if args.get("service") not in SAFE_HOME_SERVICES:
        return f"{args.get('service')!r} is not a one-tap service"
    if args.get("data"):
        # Brightness, colour, volume level: not a fixed call. Keep shortcuts
        # to the plain on/off/toggle the member keeps repeating.
        return "carries service data"
    return None


async def _run_call_service(args: dict, _member: dict) -> str:
    return await invoke("home.call_service", {
        "domain": args["domain"], "service": args["service"],
        "entity_id": args["entity_id"], "data": {},
    })


async def _run_get_state(args: dict, _member: dict) -> str:
    return await invoke("home.get_state", {"entity_id": args["entity_id"]})


def _object_name(args: dict) -> str:
    entity = str(args.get("entity_id") or "")
    domain, _, object_id = entity.partition(".")
    return f"{object_id.replace('_', '-')}-{domain.replace('_', '-')}"


def _health(tool: str) -> FastAction:
    def check(args: dict) -> str | None:
        days = args.get("days", 1)
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_HEALTH_DAYS:
            return "days out of range"
        return None

    async def run(args: dict, member: dict) -> str:
        return await invoke(f"health.{tool}", {"member_sub": member["sub"], "days": int(args.get("days", 1))})

    noun = tool.removeprefix("get_")
    return FastAction(
        specialist="health", tool=tool, permission="health", variant_key="days", write=False,
        check=check, run=run, name=lambda _a: f"my-{noun}",
        describe=lambda _a, options: f"read your {noun} data (days: {', '.join(options)})",
    )


FAST_ACTIONS: dict[tuple[str, str], FastAction] = {
    ("home", "call_service"): FastAction(
        specialist="home", tool="call_service", permission="home.control", variant_key="service",
        write=True, check=_check_call_service, run=_run_call_service, name=_object_name,
        describe=lambda a, options: f"{' / '.join(options)} {a.get('entity_id')}",
    ),
    ("home", "get_state"): FastAction(
        specialist="home", tool="get_state", permission="home.control", variant_key=None,
        write=False, check=_entity_ok, run=_run_get_state,
        name=lambda a: f"{_object_name(a)}-state",
        describe=lambda a, _o: f"check the state of {a.get('entity_id')}",
    ),
    **{("health", t): _health(t) for t in ("get_sleep", "get_recovery", "get_activity")},
}


def lookup(specialist: str, tool: str) -> FastAction | None:
    return FAST_ACTIONS.get((specialist, tool))


def normalise(action: FastAction, args: dict) -> dict:
    """The arguments that identify a call, with defaults filled in so two
    calls that mean the same thing fingerprint the same."""
    if action.specialist == "health":
        return {"days": args.get("days", 1)}
    if action.tool == "call_service":
        return {"domain": args.get("domain"), "service": args.get("service"),
                "entity_id": args.get("entity_id")}
    return {"entity_id": args.get("entity_id")}
