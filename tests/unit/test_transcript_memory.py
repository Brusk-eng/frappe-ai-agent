"""A turn is stored as the model saw it and replayed as the next turn's memory."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from test_agent_loop import _drain, _fake_llm_with_yields, _FakeRegistry
from test_confirmed_turn import STORED, _run_confirmed

from ai_agent.agent.loop import run_agent_loop
from ai_agent.agent.transcript import (
    ALLOWED_NOTE,
    ENTRY_CHARS,
    MAX_ENTRIES,
    PAUSED_NOTE,
    deserialize,
    serialize,
)
from ai_agent.integrations.frappe_history import FrappeHistoryClient
from ai_agent.services.chat import history_from_rows


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"blocks": [{"type": "tool_call", "payload": {"name": name, "arguments": args}}]}


def _text(content: str) -> dict[str, Any]:
    return {"blocks": [{"type": "text", "payload": {"content": content}}]}


class _Writes(_FakeRegistry):
    def writes(self, name: str) -> bool:
        return name == "create_document"


class TestStoredForm:
    def test_round_trip_keeps_roles_and_order(self):
        turn = [
            AIMessage(content="call"),
            HumanMessage(content="result"),
            AIMessage(content="done"),
        ]
        back = deserialize(serialize(turn))
        assert [(type(m).__name__, m.content) for m in back] == [
            ("AIMessage", "call"),
            ("HumanMessage", "result"),
            ("AIMessage", "done"),
        ]

    def test_a_long_entry_is_cut_with_a_marker(self):
        back = deserialize(serialize([HumanMessage(content="x" * (ENTRY_CHARS + 500))]))
        assert len(str(back[0].content)) < ENTRY_CHARS + 100
        assert "cut to" in str(back[0].content)

    def test_a_long_turn_keeps_its_start_and_its_latest_steps(self):
        turn: list[BaseMessage] = [
            HumanMessage(content=f"step {i}") for i in range(MAX_ENTRIES + 10)
        ]
        back = deserialize(serialize(turn))
        assert len(back) == MAX_ENTRIES
        assert back[0].content == "step 0" and back[1].content == "step 1"
        assert "earlier steps of this turn not kept" in str(back[2].content)
        assert back[-1].content == f"step {MAX_ENTRIES + 9}"

    def test_nothing_to_keep_and_unreadable_rows(self):
        assert serialize([]) is None
        for bad in ("{not json", "{}", "[1, 2]", '[{"role": "system", "content": "x"}]'):
            assert deserialize(bad) == []


class TestTheLoopRecordsTheTurn:
    @pytest.mark.asyncio
    async def test_calls_results_and_reply_are_recorded(self):
        llm = _fake_llm_with_yields(
            [[_call("web_search", {"query": "q"})], [_text("Found two pages.")]]
        )
        record: list[BaseMessage] = []
        await _drain(
            run_agent_loop(
                llm=llm,
                tool_registry=_Writes({"web_search": "Example page <https://a.test/>"}),
                user_message="search",
                record=record,
            )
        )
        kinds = [type(m).__name__ for m in record]
        assert kinds == ["AIMessage", "HumanMessage", "AIMessage"]
        assert "web_search" in str(record[0].content)
        assert "https://a.test/" in str(record[1].content)
        assert "Found two pages." in str(record[2].content)

    @pytest.mark.asyncio
    async def test_a_pause_is_recorded_as_not_done(self):
        create = _call("create_document", {"doctype": "Task", "data": {"subject": "Examplia"}})
        record: list[BaseMessage] = []
        await _drain(
            run_agent_loop(
                llm=_fake_llm_with_yields([[create]]),
                tool_registry=_Writes({"create_document": "x"}),
                user_message="add",
                record=record,
            )
        )
        assert "create_document" in str(record[-2].content)
        assert record[-1].content == PAUSED_NOTE

    @pytest.mark.asyncio
    async def test_a_turn_that_fails_still_keeps_what_it_did(self):
        llm = _fake_llm_with_yields([[_call("web_search", {"query": "q"})]])  # then runs dry
        record: list[BaseMessage] = []
        with pytest.raises(RuntimeError):
            await _drain(
                run_agent_loop(
                    llm=llm,
                    tool_registry=_Writes({"web_search": "page"}),
                    user_message="search",
                    record=record,
                )
            )
        assert any("web_search" in str(m.content) for m in record)


class TestMemoryFromRows:
    @staticmethod
    def _turn(text: str, n: int = 1) -> str:
        return serialize([AIMessage(content=f"{text} call {i}") for i in range(n)]) or ""

    def test_recent_turns_replay_in_full_and_older_ones_as_text(self):
        rows = [
            {"role": "user", "content": "first question", "transcript": ""},
            {"role": "assistant", "content": "first answer", "transcript": self._turn("old", 30)},
            {"role": "user", "content": "second question", "transcript": ""},
            {"role": "assistant", "content": "second answer", "transcript": self._turn("new")},
        ]
        memory = history_from_rows(rows, budget=200, text_limit=1000)
        contents = [str(m.content) for m in memory]
        assert contents == ["first question", "first answer", "second question", "new call 0"]

    def test_rows_without_a_transcript_are_replayed_as_their_text(self):
        rows = [
            {"role": "user", "content": "q", "transcript": ""},
            {"role": "assistant", "content": "a", "transcript": ""},
        ]
        memory = history_from_rows(rows, budget=1000, text_limit=1000)
        assert [type(m).__name__ for m in memory] == ["HumanMessage", "AIMessage"]

    def test_once_a_turn_does_not_fit_no_older_turn_is_replayed_in_full(self):
        rows = [
            {"role": "assistant", "content": "oldest", "transcript": self._turn("tiny")},
            {"role": "assistant", "content": "middle", "transcript": self._turn("big", 30)},
            {"role": "assistant", "content": "newest", "transcript": self._turn("tiny")},
        ]
        contents = [str(m.content) for m in history_from_rows(rows, budget=100, text_limit=1000)]
        assert contents == ["oldest", "middle", "tiny call 0"]


class TestStorage:
    @respx.mock
    async def test_the_transcript_is_saved_and_read_back(self):
        base = "http://frappe.test"
        respx.get(f"{base}/app").mock(return_value=httpx.Response(200, text='csrf_token = "abc"'))
        create = respx.post(f"{base}/api/resource/AI Chat Message").mock(
            return_value=httpx.Response(200, json={"data": {"name": "m1"}})
        )
        stored = serialize([AIMessage(content="call"), HumanMessage(content="result")])
        respx.get(f"{base}/api/method/frappe.client.get_list").mock(
            return_value=httpx.Response(
                200,
                json={"message": [{"role": "assistant", "content": "", "transcript_json": stored}]},
            )
        )
        client = FrappeHistoryClient(base_url=base)
        try:
            await client.save_message(
                sid="s", session="c", role="assistant", content="a", transcript_json=stored
            )
            rows = await client.list_messages(sid="s", session="c")
        finally:
            await client.aclose()
        assert json.loads(create.calls.last.request.content)["transcript_json"] == stored
        assert rows == [{"role": "assistant", "content": "", "transcript": stored}]

    async def test_a_confirmed_turn_is_saved_with_what_the_user_allowed(self):
        saved: list[dict[str, Any]] = []
        await _run_confirmed(saved, [], [], [])
        entries = json.loads(saved[-1]["transcript_json"])
        assert entries[0] == {"role": "human", "content": ALLOWED_NOTE}
        assert any(STORED["tool"] in e["content"] for e in entries[1:])
