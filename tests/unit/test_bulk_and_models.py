"""A batch is checked row by row and approved once; a turn runs on the model it chose."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_agent_loop import _drain, _fake_llm_with_yields, _FakeRegistry

from ai_agent.agent.loop import confirm_summary, run_agent_loop
from ai_agent.agent.skills import assess_risk, load_bundled_skills, skill_for_write
from ai_agent.agent.tool_registry import call_outcome
from ai_agent.config import Settings
from ai_agent.middleware.sid import UserContext, require_sid
from ai_agent.services.chat import ChatService
from ai_agent.services.health import HealthService
from ai_agent.transport.rest import create_rest_router

_META = (
    'Retrieved DocType document: Task\n{"name": "Task", "fields": ['
    '{"fieldname": "subject", "fieldtype": "Data"},'
    '{"fieldname": "project", "fieldtype": "Link"}]}'
)


class _Writes(_FakeRegistry):
    def writes(self, name: str) -> bool:
        return name in ("create_document", "create_documents")


def _batch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    call = {"name": "create_documents", "arguments": {"doctype": "Task", "rows": rows}}
    return {"blocks": [{"type": "tool_call", "payload": call}]}


# The model reads the DocType first, so these tests are about the batch and not the field brief.
_READ = {
    "blocks": [
        {
            "type": "tool_call",
            "payload": {
                "name": "get_document",
                "arguments": {"doctype": "DocType", "name": "Task"},
            },
        }
    ]
}


def _settings(**over) -> Settings:
    return Settings(_env_file=None, **over)  # pyright: ignore[reportCallIssue]


class TestBatches:
    def test_the_card_says_how_many(self):
        args = {"doctype": "Task", "rows": [{"subject": "a"}, {"subject": "b"}]}
        assert confirm_summary("create_documents", args) == "Create 2 new Task records."
        assert confirm_summary("create_documents", {"doctype": "Task", "rows": [{}]}) == (
            "Create 1 new Task record."
        )

    @pytest.mark.asyncio
    async def test_a_good_batch_pauses_once_for_the_whole_list(self):
        rows = [{"subject": f"Examplia {i}", "project": "PROJ-1"} for i in range(25)]
        llm = _fake_llm_with_yields([[_READ], [_batch(rows)]])
        reg = _Writes({"get_document": _META, "create_documents": "x"})
        events = await _drain(run_agent_loop(llm=llm, tool_registry=reg, user_message="add"))
        confirms = [e for e in events if e["type"] == "tool_confirm"]
        assert len(confirms) == 1 and len(confirms[0]["arguments"]["rows"]) == 25
        assert "Create 25 new Task records." in "".join(
            e.get("text", "") for e in events if e["type"] == "content"
        )

    @pytest.mark.asyncio
    async def test_a_bad_row_is_named_and_the_batch_goes_back(self):
        seen: list[Any] = []
        bad = [{"subject": "ok"}, {"subjekt": "typo"}]
        good = [{"subject": "ok"}, {"subject": "fixed"}]
        llm = _fake_llm_with_yields([[_READ], [_batch(bad)], [_batch(good)]])
        structured = llm.with_structured_output.return_value
        original = structured.astream
        structured.astream = lambda m, config=None: (seen.append(list(m)), original(m, config))[1]
        reg = _Writes({"get_document": _META, "create_documents": "x"})
        events = await _drain(run_agent_loop(llm=llm, tool_registry=reg, user_message="add"))
        note = str(seen[2][-1].content)
        assert "Row 2: Task has no field named 'subjekt'" in note
        assert "Row 1:" not in note
        assert [e["type"] for e in events].count("tool_confirm") == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rows", "says"),
        [([], "non-empty list"), (["x"], "non-empty list"), ([{}], "non-empty list")],
    )
    async def test_a_malformed_batch_goes_back(self, rows, says):
        seen: list[Any] = []
        llm = _fake_llm_with_yields([[_READ], [_batch(rows)], [_batch([{"subject": "a"}])]])
        structured = llm.with_structured_output.return_value
        original = structured.astream
        structured.astream = lambda m, config=None: (seen.append(list(m)), original(m, config))[1]
        reg = _Writes({"get_document": _META, "create_documents": "x"})
        await _drain(run_agent_loop(llm=llm, tool_registry=reg, user_message="add"))
        assert says in str(seen[2][-1].content)

    @pytest.mark.asyncio
    async def test_over_the_cap_is_split(self):
        seen: list[Any] = []
        llm = _fake_llm_with_yields(
            [
                [_READ],
                [_batch([{"subject": str(i)} for i in range(101)])],
                [_batch([{"subject": "a"}])],
            ]
        )
        structured = llm.with_structured_output.return_value
        original = structured.astream
        structured.astream = lambda m, config=None: (seen.append(list(m)), original(m, config))[1]
        await _drain(
            run_agent_loop(
                llm=llm,
                tool_registry=_Writes({"get_document": _META, "create_documents": "x"}),
                user_message="add",
            )
        )
        assert "split the batch" in str(seen[2][-1].content)

    def test_a_batch_of_users_always_asks_and_has_its_skill(self):
        args = {"doctype": "User", "rows": [{"email": "a@example.org"}]}
        assert assess_risk("create_documents", args) == "always-ask"
        skill = skill_for_write(load_bundled_skills(), "create_documents", args)
        assert skill is not None and skill.name == "user-create"

    def test_the_new_names_are_remembered(self):
        result = "Successfully created 3 Task documents: TASK-1, TASK-2, TASK-3\n[...]"
        outcome = call_outcome("create_documents", result, failed=False)
        assert outcome == "Successfully created 3 Task documents: TASK-1, TASK-2, TASK-3"


class TestModels:
    """The menu is what the provider lists, never a list typed into the config."""

    _OPENROUTER: ClassVar[dict[str, Any]] = {
        "data": [
            {
                "id": "p/a",
                "name": "Model A",
                "supported_parameters": ["tools", "structured_outputs"],
            },
            {"id": "p/b", "name": "Model B", "supported_parameters": ["response_format"]},
            {"id": "p/no-schema", "name": "No Schema", "supported_parameters": ["tools"]},
            {"id": "q/c", "name": "Model C"},
        ]
    }

    @staticmethod
    def _catalog(**over):
        from ai_agent.integrations.models import ModelCatalog

        base = {"llm_provider": "openai", "llm_base_url": "https://provider.test/api/v1"}
        return ModelCatalog(_settings(**{**base, **over}))

    @respx.mock
    async def test_the_providers_list_is_the_menu_default_first(self):
        route = respx.get("https://provider.test/api/v1/models").mock(
            return_value=httpx.Response(200, json=self._OPENROUTER)
        )
        catalog = self._catalog(llm_model="p/b", llm_api_key="k")
        models = await catalog.models()
        assert [(m.id, m.label) for m in models] == [
            ("p/b", "Model B"),
            ("p/a", "Model A"),
            ("q/c", "Model C"),
        ]
        assert route.calls.last.request.headers["authorization"] == "Bearer k"
        await catalog.models()
        assert route.call_count == 1  # cached

    @respx.mock
    async def test_the_filter_narrows_by_prefix_or_exact_id(self):
        respx.get("https://provider.test/api/v1/models").mock(
            return_value=httpx.Response(200, json=self._OPENROUTER)
        )
        models = await self._catalog(llm_model="p/a", llm_models_filter="q/, p/a").models()
        assert [m.id for m in models] == ["p/a", "q/c"]

    @respx.mock
    async def test_an_unreachable_provider_leaves_only_the_default(self):
        respx.get("https://provider.test/api/v1/models").mock(return_value=httpx.Response(503))
        models = await self._catalog(llm_model="p/a").models()
        assert [(m.id, m.label) for m in models] == [("p/a", "p/a")]

    @respx.mock
    async def test_ollama_is_asked_for_its_tags(self):
        respx.get("http://ollama.test:11434/api/tags").mock(
            return_value=httpx.Response(
                200, json={"models": [{"name": "qwen3.5:9b"}, {"name": "llama"}]}
            )
        )
        models = await self._catalog(
            llm_provider="ollama", llm_base_url="http://ollama.test:11434", llm_model="qwen3.5:9b"
        ).models()
        assert [m.id for m in models] == ["qwen3.5:9b", "llama"]

    @respx.mock
    async def test_the_sidebar_lists_them(self):
        respx.get("https://provider.test/api/v1/models").mock(
            return_value=httpx.Response(200, json=self._OPENROUTER)
        )
        app = FastAPI()
        catalog = self._catalog(llm_model="p/a")
        app.include_router(
            create_rest_router(
                settings=_settings(llm_model="p/a"),
                health_service=AsyncMock(spec=HealthService),
                models=catalog,
            )
        )
        app.dependency_overrides[require_sid] = lambda: UserContext(sid="s")
        body = TestClient(app).get("/api/v1/models").json()
        assert body["default"] == "p/a"
        assert [m["id"] for m in body["models"]] == ["p/a", "p/b", "q/c"]

    @respx.mock
    async def test_a_turn_runs_on_its_model_and_only_an_offered_one(self):
        respx.get("https://provider.test/api/v1/models").mock(
            return_value=httpx.Response(200, json=self._OPENROUTER)
        )
        default, chosen = MagicMock(name="default"), MagicMock(name="chosen")
        service = ChatService(
            settings=_settings(llm_model="p/a"),
            llm=default,
            history=MagicMock(),
            models=self._catalog(llm_model="p/a"),
        )
        with patch("ai_agent.services.chat.create_llm", return_value=chosen) as made:
            assert await service._llm_for("p/b") is chosen
            assert await service._llm_for("p/b") is chosen  # made once, then reused
            assert made.call_count == 1
            assert made.call_args.args[0].llm_model == "p/b"
            assert await service._llm_for("p/no-schema") is default  # the provider says it cannot
            assert await service._llm_for("someone/else") is default
            assert await service._llm_for(None) is default
