"""The optional usage gate (integrations/usage.py) on the chat route."""

from __future__ import annotations

import json

import httpx
import respx
from httpx import ASGITransport, AsyncClient

from ai_agent.app import create_app
from ai_agent.config import Settings

FRAPPE = "http://frappe.test"
WHO = f"{FRAPPE}/api/method/frappe.auth.get_logged_user"
GATE = "http://usage.test/check"


class _Answers:
    def __init__(self) -> None:
        self.asked = 0

    async def handle_message(
        self, *, message, session_id, context, user_context, confirmation=None
    ):
        self.asked += 1
        yield {"type": "content", "text": "hi"}
        yield {"type": "done", "tools_called": [], "data_quality": "high", "timestamp": "t"}


async def _chat(body: dict | None = None, **settings) -> tuple[httpx.Response, _Answers]:
    app = create_app(Settings(_env_file=None, frappe_url=FRAPPE, **settings))  # pyright: ignore[reportCallIssue]
    answers = _Answers()
    app.state.chat_service = answers
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"sid": "alice-sid"},
        headers={"X-Frappe-Site-Name": "crm-acme.example.test"},
    ) as ac:
        response = await ac.post("/api/v1/chat", json=body or {"message": "hello"})
    return response, answers


def _events(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def _signed_in():
    respx.get(WHO).mock(return_value=httpx.Response(200, json={"message": "alice@example.test"}))


@respx.mock
async def test_without_a_gate_url_nothing_is_checked():
    _signed_in()
    gate = respx.post(GATE)
    response, answers = await _chat()
    assert response.status_code == 200
    assert answers.asked == 1
    assert not gate.called


@respx.mock
async def test_the_usage_service_is_asked_with_the_site_user_and_token():
    _signed_in()
    gate = respx.post(GATE).mock(
        return_value=httpx.Response(200, json={"message": {"allowed": True}})
    )
    _, answers = await _chat(usage_check_url=GATE, usage_check_token="svc-token")
    assert answers.asked == 1
    sent = gate.calls.last.request
    assert json.loads(sent.content) == {
        "site": "crm-acme.example.test",
        "user": "alice@example.test",
    }
    assert sent.headers["x-usage-token"] == "svc-token"
    assert "authorization" not in sent.headers


@respx.mock
async def test_a_refusal_streams_the_services_reason_and_runs_nothing():
    _signed_in()
    reason = "You have used today's 50 AI Assistant messages on this site."
    respx.post(GATE).mock(
        return_value=httpx.Response(200, json={"message": {"allowed": False, "reason": reason}})
    )
    response, answers = await _chat(usage_check_url=GATE)
    assert response.status_code == 200
    assert answers.asked == 0
    assert _events(response) == [{"type": "error", "message": reason}]


@respx.mock
async def test_an_unreachable_usage_service_lets_the_message_through():
    _signed_in()
    respx.post(GATE).mock(side_effect=httpx.ConnectError("down"))
    _, answers = await _chat(usage_check_url=GATE)
    assert answers.asked == 1
    respx.post(GATE).mock(return_value=httpx.Response(503))
    _, answers = await _chat(usage_check_url=GATE)
    assert answers.asked == 1


@respx.mock
async def test_a_refused_token_is_a_refusal_not_a_pass():
    _signed_in()
    respx.post(GATE).mock(return_value=httpx.Response(403, json={"exc_type": "PermissionError"}))
    response, answers = await _chat(usage_check_url=GATE)
    assert answers.asked == 0
    assert _events(response)[0]["type"] == "error"


@respx.mock
async def test_allowing_a_proposed_change_is_never_checked():
    _signed_in()
    gate = respx.post(GATE).mock(
        return_value=httpx.Response(200, json={"message": {"allowed": False}})
    )
    body = {
        "session_id": "s",
        "confirmation": {"tool": "create_doc", "arguments": {}, "token": "t"},
    }
    _, answers = await _chat(body, usage_check_url=GATE)
    assert not gate.called
    assert answers.asked == 1
