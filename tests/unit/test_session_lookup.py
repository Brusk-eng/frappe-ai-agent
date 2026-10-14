"""A session is looked up before it is created, so Frappe's error log fills with no duplicates."""

from __future__ import annotations

import respx
from httpx import Response

from ai_agent.integrations.frappe_history import FrappeHistoryClient

FRAPPE = "http://frappe.test"
LIST = f"{FRAPPE}/api/method/frappe.client.get_list"
CREATE = f"{FRAPPE}/api/resource/AI Chat Session"


def _client() -> FrappeHistoryClient:
    return FrappeHistoryClient(base_url=FRAPPE)


async def _ensure(client: FrappeHistoryClient, name: str = "chat-1", site: str | None = None):
    return await client.ensure_session(sid="s", name=name, title="t", context_json="{}", site=site)


@respx.mock
async def test_an_existing_session_is_not_created_again():
    lookup = respx.get(LIST).mock(
        return_value=Response(200, json={"message": [{"name": "chat-1"}]})
    )
    create = respx.post(CREATE).mock(return_value=Response(409))
    client = _client()
    try:
        assert await _ensure(client) == "chat-1"
    finally:
        await client.aclose()
    assert lookup.call_count == 1
    assert create.call_count == 0


@respx.mock
async def test_a_missing_session_is_created_once_and_then_remembered():
    respx.get(f"{FRAPPE}/app").mock(return_value=Response(200, text='csrf_token = "abc123"'))
    lookup = respx.get(LIST).mock(return_value=Response(200, json={"message": []}))
    create = respx.post(CREATE).mock(return_value=Response(200, json={"data": {"name": "chat-1"}}))
    client = _client()
    try:
        assert await _ensure(client) == "chat-1"
        assert await _ensure(client) == "chat-1"  # next turn: neither looked up nor created
    finally:
        await client.aclose()
    assert lookup.call_count == 1
    assert create.call_count == 1


@respx.mock
async def test_memory_is_per_site():
    lookup = respx.get(LIST).mock(
        return_value=Response(200, json={"message": [{"name": "chat-1"}]})
    )
    client = _client()
    try:
        await _ensure(client, site="a.test")
        await _ensure(client, site="b.test")
        await _ensure(client, site="a.test")
    finally:
        await client.aclose()
    assert lookup.call_count == 2


@respx.mock
async def test_a_failed_lookup_falls_back_to_the_create_attempt():
    respx.get(f"{FRAPPE}/app").mock(return_value=Response(200, text='csrf_token = "abc123"'))
    respx.get(LIST).mock(return_value=Response(500))
    create = respx.post(CREATE).mock(return_value=Response(409))
    client = _client()
    try:
        assert await _ensure(client) == "chat-1"  # the id is kept, as before
    finally:
        await client.aclose()
    assert create.call_count == 1
