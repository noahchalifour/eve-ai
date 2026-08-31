import httpx
import pytest
import respx

from eve_tools import home_assistant


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_HOME_ASSISTANT_URL", "http://ha.test")
    monkeypatch.setenv("EVE_TOOLS_HOME_ASSISTANT_TOKEN", "ha-token")


@respx.mock
async def test_get_state_reads_from_home_assistant():
    respx.get("http://ha.test/api/states/light.kitchen").mock(
        return_value=httpx.Response(200, json={"entity_id": "light.kitchen", "state": "on"})
    )
    result = await home_assistant.get_state("light.kitchen")
    assert result["state"] == "on"


@respx.mock
async def test_get_state_sends_the_bearer_token():
    route = respx.get("http://ha.test/api/states/light.kitchen").mock(
        return_value=httpx.Response(200, json={})
    )
    await home_assistant.get_state("light.kitchen")
    assert route.calls.last.request.headers["authorization"] == "Bearer ha-token"


@respx.mock
async def test_call_service_posts_to_home_assistant():
    route = respx.post("http://ha.test/api/services/light/turn_on").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = await home_assistant.call_service(
        "light", "turn_on", "light.kitchen", {"brightness": 200}
    )
    assert result["called"] is True
    body = route.calls.last.request.content
    import json as _json
    assert _json.loads(body) == {"entity_id": "light.kitchen", "brightness": 200}


_ALL_STATES = [
    {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {"friendly_name": "Kitchen", "brightness": 254},
    },
    {"entity_id": "light.porch", "state": "off", "attributes": {}},
    {"entity_id": "sensor.outside_temp", "state": "11.4", "attributes": {}},
]


@respx.mock
async def test_list_entities_returns_every_domain_by_default():
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=_ALL_STATES)
    )
    result = await home_assistant.list_entities()
    assert result["total"] == 3
    assert [e["entity_id"] for e in result["entities"]] == [
        "light.kitchen",
        "light.porch",
        "sensor.outside_temp",
    ]


@respx.mock
async def test_list_entities_filters_to_one_domain():
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=_ALL_STATES)
    )
    result = await home_assistant.list_entities("light")
    assert result["total"] == 2
    assert {e["entity_id"] for e in result["entities"]} == {
        "light.kitchen",
        "light.porch",
    }


@respx.mock
async def test_list_entities_trims_the_attribute_blob_to_a_friendly_name():
    """HA sends every attribute of every entity. Forwarding that wholesale
    would spend the specialist's context on brightness values and colour
    temperatures it never reasons about."""
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=_ALL_STATES)
    )
    result = await home_assistant.list_entities("light")
    kitchen = next(e for e in result["entities"] if e["entity_id"] == "light.kitchen")
    assert kitchen == {"entity_id": "light.kitchen", "state": "on", "name": "Kitchen"}


@respx.mock
async def test_list_entities_caps_the_list_and_says_so():
    many = [
        {"entity_id": f"light.l{i}", "state": "off", "attributes": {}}
        for i in range(home_assistant._MAX_ENTITIES + 5)
    ]
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=many)
    )
    result = await home_assistant.list_entities("light")
    assert len(result["entities"]) == home_assistant._MAX_ENTITIES
    assert result["total"] == home_assistant._MAX_ENTITIES + 5
    assert result["truncated"] is True


@respx.mock
async def test_list_entities_sends_the_bearer_token():
    route = respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=[])
    )
    await home_assistant.list_entities()
    assert route.calls.last.request.headers["authorization"] == "Bearer ha-token"


@respx.mock
async def test_weather_returns_current_conditions_and_both_ranges():
    respx.get("http://ha.test/api/states/weather.home").mock(
        return_value=httpx.Response(
            200,
            json={
                "entity_id": "weather.home",
                "state": "partlycloudy",
                "attributes": {"friendly_name": "Home", "temperature": 21.4},
            },
        )
    )
    respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(
            200,
            json={
                "service_response": {
                    "weather.home": {
                        "forecast": [
                            {
                                "datetime": "2026-08-31T14:00:00+00:00",
                                "condition": "sunny",
                                "temperature": 22,
                            }
                        ]
                    }
                }
            },
        )
    )

    result = await home_assistant.weather("weather.home")

    assert result["entity_id"] == "weather.home"
    assert result["location"] == "Home"
    assert result["condition"] == "partlycloudy"
    assert result["temperature"] == 21.4
    # Both ranges come back, from one relay call: the card needs the hourly
    # strip now and `ui_action` needs the daily one on the next turn.
    assert result["hourly"][0]["temperature"] == 22
    assert result["daily"][0]["temperature"] == 22


@respx.mock
async def test_weather_asks_for_the_response_body():
    """Without `?return_response` HA answers 200 with an empty body and the
    forecast is silently lost. This assertion is the whole reason this
    function exists instead of a `call_service` call."""
    respx.get("http://ha.test/api/states/weather.home").mock(
        return_value=httpx.Response(
            200, json={"state": "sunny", "attributes": {"temperature": 20}}
        )
    )
    route = respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(200, json={"service_response": {}})
    )

    await home_assistant.weather("weather.home")

    assert "return_response" in str(route.calls[0].request.url)


@respx.mock
async def test_weather_discovers_the_entity_when_none_is_named():
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
                {"entity_id": "weather.cottage", "state": "rainy", "attributes": {}},
            ],
        )
    )
    respx.get("http://ha.test/api/states/weather.cottage").mock(
        return_value=httpx.Response(
            200, json={"state": "rainy", "attributes": {"temperature": 12}}
        )
    )
    respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(200, json={"service_response": {}})
    )

    result = await home_assistant.weather()

    assert result["entity_id"] == "weather.cottage"


@respx.mock
async def test_weather_survives_a_range_the_entity_does_not_publish():
    """Plenty of HA weather integrations have no hourly forecast. That is an
    empty range, not a failed call - the card still renders, and the client
    dispatches a remote action for whichever range is absent."""
    respx.get("http://ha.test/api/states/weather.home").mock(
        return_value=httpx.Response(
            200, json={"state": "sunny", "attributes": {"temperature": 20}}
        )
    )
    respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(500, json={"message": "not supported"})
    )

    result = await home_assistant.weather("weather.home")

    assert result["hourly"] == []
    assert result["daily"] == []


@respx.mock
async def test_weather_raises_when_the_home_has_no_weather_entity():
    """`eve_tools.app.invoke_tool` turns a raised exception into
    `{"error": ...}` with a 200, which `eve.tools_client.invoke` hands back as
    an `error: ...` string. Raising is how this reaches the caller."""
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=[])
    )

    with pytest.raises(ValueError):
        await home_assistant.weather()
