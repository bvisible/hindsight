"""Unit tests for Hindsight Haystack tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hindsight_haystack import (
    configure,
    create_hindsight_tools,
    reset_config,
)
from hindsight_haystack.errors import HindsightError


def _mock_client():
    """Create a mock Hindsight client with async methods."""
    client = MagicMock()
    client.aretain = AsyncMock()
    client.arecall = AsyncMock()
    client.areflect = AsyncMock()
    client.acreate_bank = AsyncMock()
    return client


def _mock_recall_response(texts: list[str]):
    response = MagicMock()
    results = []
    for t in texts:
        r = MagicMock()
        r.text = t
        results.append(r)
    response.results = results
    return response


def _mock_reflect_response(text: str):
    response = MagicMock()
    response.text = text
    return response


def _mock_retain_response():
    response = MagicMock()
    response.success = True
    return response


class TestCreateHindsightTools:
    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_returns_three_tools_by_default(self):
        client = _mock_client()
        tools = create_hindsight_tools(bank_id="test", client=client)
        assert len(tools) == 3

    def test_include_retain_only(self):
        client = _mock_client()
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            include_retain=True,
            include_recall=False,
            include_reflect=False,
        )
        assert len(tools) == 1
        assert tools[0].name == "retain_memory"

    def test_include_recall_only(self):
        client = _mock_client()
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            include_retain=False,
            include_recall=True,
            include_reflect=False,
        )
        assert len(tools) == 1
        assert tools[0].name == "recall_memory"

    def test_include_reflect_only(self):
        client = _mock_client()
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            include_retain=False,
            include_recall=False,
            include_reflect=True,
        )
        assert len(tools) == 1
        assert tools[0].name == "reflect_on_memory"

    def test_no_tools_when_all_excluded(self):
        client = _mock_client()
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            include_retain=False,
            include_recall=False,
            include_reflect=False,
        )
        assert len(tools) == 0

    def test_raises_without_client_or_config(self):
        with pytest.raises(HindsightError, match="No Hindsight API URL"):
            create_hindsight_tools(bank_id="test")

    def test_falls_back_to_global_config(self):
        configure(hindsight_api_url="http://localhost:8888")
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_cls.return_value = _mock_client()
            tools = create_hindsight_tools(bank_id="test")
            assert len(tools) == 3
            mock_cls.assert_called_once()
            assert mock_cls.call_args.kwargs["base_url"] == "http://localhost:8888"
            assert mock_cls.call_args.kwargs["timeout"] == 30.0

    def test_explicit_url_overrides_config(self):
        configure(hindsight_api_url="http://config:8888")
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_cls.return_value = _mock_client()
            create_hindsight_tools(
                bank_id="test", hindsight_api_url="http://explicit:9999"
            )
            mock_cls.assert_called_once()
            assert mock_cls.call_args.kwargs["base_url"] == "http://explicit:9999"
            assert mock_cls.call_args.kwargs["timeout"] == 30.0


class TestRetainTool:
    def test_retain_stores_memory(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(bank_id="test-bank", client=client)
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        result = retain_tool.invoke(content="The user likes Python")
        assert result == "Memory stored successfully."
        call_kwargs = client.aretain.call_args[1]
        assert call_kwargs["bank_id"] == "test-bank"
        assert call_kwargs["content"] == "The user likes Python"
        assert call_kwargs["context"] == "haystack"

    def test_retain_passes_tags(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(
            bank_id="test-bank", client=client, tags=["source:chat"]
        )
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="some content")
        call_kwargs = client.aretain.call_args[1]
        assert call_kwargs["tags"] == ["source:chat"]

    def test_retain_passes_metadata(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            retain_metadata={"source": "chat", "session": "abc"},
        )
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="content")
        call_kwargs = client.aretain.call_args[1]
        assert call_kwargs["metadata"] == {"source": "chat", "session": "abc"}

    def test_retain_passes_explicit_document_id(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(
            bank_id="test", client=client, retain_document_id="session-123"
        )
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="content")
        call_kwargs = client.aretain.call_args[1]
        assert call_kwargs["document_id"] == "session-123"

    def test_retain_auto_generates_document_id(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(bank_id="test", client=client)
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="content")
        call_kwargs = client.aretain.call_args[1]
        doc_id = call_kwargs["document_id"]
        # Auto-generated format: {session_id}-{uuid_hex_12}
        assert "-" in doc_id
        suffix = doc_id.rsplit("-", 1)[1]
        assert len(suffix) == 12

    def test_retain_passes_context_label(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(
            bank_id="test", client=client, retain_context="my-app"
        )
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="content")
        call_kwargs = client.aretain.call_args[1]
        assert call_kwargs["context"] == "my-app"

    def test_retain_defaults_to_haystack_context(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(bank_id="test", client=client)
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="content")
        call_kwargs = client.aretain.call_args[1]
        assert call_kwargs["context"] == "haystack"

    def test_retain_returns_error_message_on_failure(self):
        """Errors are returned gracefully, not raised."""
        client = _mock_client()
        client.aretain.side_effect = RuntimeError("connection refused")
        tools = create_hindsight_tools(bank_id="test", client=client)
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        result = retain_tool.invoke(content="content")
        assert "Failed to store memory" in result
        assert "connection refused" in result


class TestRecallTool:
    def test_recall_returns_numbered_results(self):
        client = _mock_client()
        client.arecall.return_value = _mock_recall_response(
            ["User likes Python", "User is in NYC"]
        )
        tools = create_hindsight_tools(bank_id="test-bank", client=client)
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        result = recall_tool.invoke(query="user preferences")
        assert "1. User likes Python" in result
        assert "2. User is in NYC" in result

    def test_recall_empty_results(self):
        client = _mock_client()
        client.arecall.return_value = _mock_recall_response([])
        tools = create_hindsight_tools(bank_id="test", client=client)
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        result = recall_tool.invoke(query="anything")
        assert result == "No relevant memories found."

    def test_recall_passes_budget_and_max_tokens(self):
        client = _mock_client()
        client.arecall.return_value = _mock_recall_response(["fact"])
        tools = create_hindsight_tools(
            bank_id="test", client=client, budget="high", max_tokens=2048
        )
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        recall_tool.invoke(query="query")
        call_kwargs = client.arecall.call_args[1]
        assert call_kwargs["budget"] == "high"
        assert call_kwargs["max_tokens"] == 2048

    def test_recall_passes_tags(self):
        client = _mock_client()
        client.arecall.return_value = _mock_recall_response(["fact"])
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            recall_tags=["scope:user"],
            recall_tags_match="all",
        )
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        recall_tool.invoke(query="query")
        call_kwargs = client.arecall.call_args[1]
        assert call_kwargs["tags"] == ["scope:user"]
        assert call_kwargs["tags_match"] == "all"

    def test_recall_passes_types(self):
        client = _mock_client()
        client.arecall.return_value = _mock_recall_response(["fact"])
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            recall_types=["world", "experience"],
        )
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        recall_tool.invoke(query="query")
        call_kwargs = client.arecall.call_args[1]
        assert call_kwargs["types"] == ["world", "experience"]

    def test_recall_passes_include_entities(self):
        client = _mock_client()
        client.arecall.return_value = _mock_recall_response(["fact"])
        tools = create_hindsight_tools(
            bank_id="test", client=client, recall_include_entities=True
        )
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        recall_tool.invoke(query="query")
        call_kwargs = client.arecall.call_args[1]
        assert call_kwargs["include_entities"] is True

    def test_recall_returns_error_message_on_failure(self):
        """Errors are returned gracefully, not raised."""
        client = _mock_client()
        client.arecall.side_effect = RuntimeError("timeout")
        tools = create_hindsight_tools(bank_id="test", client=client)
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        result = recall_tool.invoke(query="query")
        assert "Failed to search memory" in result


class TestReflectTool:
    def test_reflect_returns_text(self):
        client = _mock_client()
        client.areflect.return_value = _mock_reflect_response(
            "The user is a Python developer who prefers functional patterns."
        )
        tools = create_hindsight_tools(bank_id="test-bank", client=client)
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        result = reflect_tool.invoke(query="What do you know about the user?")
        assert (
            result == "The user is a Python developer who prefers functional patterns."
        )

    def test_reflect_empty_returns_fallback(self):
        client = _mock_client()
        client.areflect.return_value = _mock_reflect_response("")
        tools = create_hindsight_tools(bank_id="test", client=client)
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        result = reflect_tool.invoke(query="anything")
        assert result == "No relevant memories found."

    def test_reflect_passes_budget(self):
        client = _mock_client()
        client.areflect.return_value = _mock_reflect_response("answer")
        tools = create_hindsight_tools(
            bank_id="test", client=client, budget="high"
        )
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        reflect_tool.invoke(query="query")
        call_kwargs = client.areflect.call_args[1]
        assert call_kwargs["budget"] == "high"

    def test_reflect_passes_context(self):
        client = _mock_client()
        client.areflect.return_value = _mock_reflect_response("answer")
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            reflect_context="The user is asking about project setup",
        )
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        reflect_tool.invoke(query="query")
        call_kwargs = client.areflect.call_args[1]
        assert call_kwargs["context"] == "The user is asking about project setup"

    def test_reflect_passes_max_tokens_and_response_schema(self):
        client = _mock_client()
        client.areflect.return_value = _mock_reflect_response("answer")
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            reflect_max_tokens=2048,
            reflect_response_schema=schema,
        )
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        reflect_tool.invoke(query="query")
        call_kwargs = client.areflect.call_args[1]
        assert call_kwargs["max_tokens"] == 2048
        assert call_kwargs["response_schema"] == schema

    def test_reflect_passes_tags(self):
        client = _mock_client()
        client.areflect.return_value = _mock_reflect_response("answer")
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            reflect_tags=["scope:global"],
            reflect_tags_match="all",
        )
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        reflect_tool.invoke(query="query")
        call_kwargs = client.areflect.call_args[1]
        assert call_kwargs["tags"] == ["scope:global"]
        assert call_kwargs["tags_match"] == "all"

    def test_reflect_falls_back_to_recall_tags(self):
        client = _mock_client()
        client.areflect.return_value = _mock_reflect_response("answer")
        tools = create_hindsight_tools(
            bank_id="test",
            client=client,
            recall_tags=["scope:user"],
            recall_tags_match="any",
        )
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        reflect_tool.invoke(query="query")
        call_kwargs = client.areflect.call_args[1]
        assert call_kwargs["tags"] == ["scope:user"]
        assert call_kwargs["tags_match"] == "any"

    def test_reflect_returns_error_message_on_failure(self):
        """Errors are returned gracefully, not raised."""
        client = _mock_client()
        client.areflect.side_effect = RuntimeError("timeout")
        tools = create_hindsight_tools(bank_id="test", client=client)
        reflect_tool = [t for t in tools if t.name == "reflect_on_memory"][0]
        result = reflect_tool.invoke(query="query")
        assert "Failed to reflect on memory" in result


class TestBankMission:
    def test_creates_bank_with_mission_on_first_use(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(
            bank_id="test-bank", client=client, mission="Track user preferences"
        )
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="content")
        client.acreate_bank.assert_called_once_with(
            bank_id="test-bank",
            name="test-bank",
            mission="Track user preferences",
        )

    def test_bank_creation_is_idempotent(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        client.arecall.return_value = _mock_recall_response(["fact"])
        tools = create_hindsight_tools(
            bank_id="test-bank", client=client, mission="my mission"
        )
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        recall_tool = [t for t in tools if t.name == "recall_memory"][0]
        retain_tool.invoke(content="content")
        recall_tool.invoke(query="query")
        # create_bank should only be called once
        assert client.acreate_bank.call_count == 1

    def test_bank_creation_failure_is_graceful(self):
        client = _mock_client()
        client.acreate_bank.side_effect = RuntimeError("already exists")
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(
            bank_id="test-bank", client=client, mission="my mission"
        )
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        # Should not raise
        result = retain_tool.invoke(content="content")
        assert result == "Memory stored successfully."

    def test_no_bank_creation_without_mission(self):
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(bank_id="test-bank", client=client)
        retain_tool = [t for t in tools if t.name == "retain_memory"][0]
        retain_tool.invoke(content="content")
        client.acreate_bank.assert_not_called()

    def test_mission_from_config(self):
        reset_config()
        configure(
            hindsight_api_url="http://localhost:8888",
            mission="config mission",
        )
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_instance = _mock_client()
            mock_cls.return_value = mock_instance
            mock_instance.aretain.return_value = _mock_retain_response()

            tools = create_hindsight_tools(bank_id="test")
            retain_tool = [t for t in tools if t.name == "retain_memory"][0]
            retain_tool.invoke(content="content")
            mock_instance.acreate_bank.assert_called_once_with(
                bank_id="test",
                name="test",
                mission="config mission",
            )
        reset_config()


class TestHaystackCompatibility:
    """Verify tools integrate correctly with Haystack's Tool interface."""

    def test_tools_have_correct_name_and_description(self):
        """Each tool should have name and description."""
        client = _mock_client()
        tools = create_hindsight_tools(bank_id="test", client=client)

        for tool in tools:
            assert tool.name is not None
            assert tool.description is not None
            assert len(tool.description) > 0

    def test_tool_names_match_expected_set(self):
        client = _mock_client()
        tools = create_hindsight_tools(bank_id="test", client=client)
        tool_names = {t.name for t in tools}
        assert tool_names == {"retain_memory", "recall_memory", "reflect_on_memory"}

    def test_tools_have_parameters_schema(self):
        """Each tool should have a valid parameters JSON schema."""
        client = _mock_client()
        tools = create_hindsight_tools(bank_id="test", client=client)

        for tool in tools:
            assert tool.parameters is not None
            assert tool.parameters["type"] == "object"
            assert "properties" in tool.parameters
            assert "required" in tool.parameters

    def test_retain_tool_callable(self):
        """Tool.invoke() should call retain_memory correctly."""
        client = _mock_client()
        client.aretain.return_value = _mock_retain_response()
        tools = create_hindsight_tools(
            bank_id="test", client=client, include_recall=False, include_reflect=False
        )
        tool = tools[0]

        result = tool.invoke(content="test memory")
        assert "stored successfully" in str(result)
        client.aretain.assert_called_once()

    def test_recall_tool_callable(self):
        """Tool.invoke() should call recall_memory correctly."""
        client = _mock_client()
        client.arecall.return_value = _mock_recall_response(["some fact"])
        tools = create_hindsight_tools(
            bank_id="test", client=client, include_retain=False, include_reflect=False
        )
        tool = tools[0]

        result = tool.invoke(query="test query")
        assert "some fact" in str(result)
        client.arecall.assert_called_once()

    def test_tools_are_serializable(self):
        """Tool.to_dict() should succeed (not raise SerializationError)."""
        client = _mock_client()
        tools = create_hindsight_tools(bank_id="test", client=client)

        for tool in tools:
            d = tool.to_dict()
            assert d["type"] == "hindsight_haystack.tools._HindsightTool"
            assert d["data"]["name"] == tool.name
            assert "backend_kwargs" in d["data"]

    def test_tools_round_trip_serialization_with_config(self):
        """Tool.from_dict(tool.to_dict()) should produce a working tool (config path)."""
        configure(hindsight_api_url="http://localhost:8888")
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_instance = _mock_client()
            mock_cls.return_value = mock_instance
            mock_instance.aretain.return_value = _mock_retain_response()

            tools = create_hindsight_tools(bank_id="test")
            retain_tool = [t for t in tools if t.name == "retain_memory"][0]

            from hindsight_haystack.tools import _HindsightTool

            d = retain_tool.to_dict()
            restored = _HindsightTool.from_dict(d)

            assert restored.name == "retain_memory"
            result = restored.invoke(content="test")
            assert result == "Memory stored successfully."
        reset_config()

    def test_tools_round_trip_serialization_with_client(self):
        """Round-trip works when tools were created with an explicit client."""
        from hindsight_client import Hindsight

        reset_config()
        # Create a real-ish client to extract base_url from
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_instance = _mock_client()
            mock_instance._base_url = "http://from-client:8888"
            mock_instance._api_key = "client-key"
            mock_cls.return_value = mock_instance

            # Pass the mock as the explicit client
            tools = create_hindsight_tools(bank_id="test", client=mock_instance)
            retain_tool = [t for t in tools if t.name == "retain_memory"][0]

            # Verify serialized kwargs captured the client's connection info
            d = retain_tool.to_dict()
            assert d["data"]["backend_kwargs"]["hindsight_api_url"] == "http://from-client:8888"
            assert d["data"]["backend_kwargs"]["api_key"] == "client-key"

            # Round-trip: from_dict should reconstruct using the serialized URL
            from hindsight_haystack.tools import _HindsightTool

            restored = _HindsightTool.from_dict(d)
            assert restored.name == "retain_memory"
            # Verify Hindsight was called with the extracted URL
            mock_cls.assert_called()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["base_url"] == "http://from-client:8888"
            assert call_kwargs["api_key"] == "client-key"

    def test_serialization_preserves_explicit_url_over_client(self):
        """When both client= and hindsight_api_url= are passed, explicit URL wins."""
        client = _mock_client()
        client._base_url = "http://from-client:8888"

        tools = create_hindsight_tools(
            bank_id="test", client=client, hindsight_api_url="http://explicit:9999"
        )
        d = tools[0].to_dict()
        # Explicit URL should take precedence
        assert d["data"]["backend_kwargs"]["hindsight_api_url"] == "http://explicit:9999"


class TestConfigFallback:
    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_budget_falls_back_to_config(self):
        configure(hindsight_api_url="http://localhost:8888")
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_instance = _mock_client()
            mock_cls.return_value = mock_instance
            mock_instance.arecall.return_value = _mock_recall_response(["fact"])

            # Configure with custom budget
            reset_config()
            configure(hindsight_api_url="http://localhost:8888", budget="high")

            tools = create_hindsight_tools(bank_id="test")
            recall_tool = [t for t in tools if t.name == "recall_memory"][0]
            recall_tool.invoke(query="query")
            call_kwargs = mock_instance.arecall.call_args[1]
            assert call_kwargs["budget"] == "high"

    def test_explicit_budget_overrides_config(self):
        configure(hindsight_api_url="http://localhost:8888", budget="high")
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_instance = _mock_client()
            mock_cls.return_value = mock_instance
            mock_instance.arecall.return_value = _mock_recall_response(["fact"])

            tools = create_hindsight_tools(bank_id="test", budget="low")
            recall_tool = [t for t in tools if t.name == "recall_memory"][0]
            recall_tool.invoke(query="query")
            call_kwargs = mock_instance.arecall.call_args[1]
            assert call_kwargs["budget"] == "low"

    def test_tags_fall_back_to_config(self):
        configure(hindsight_api_url="http://localhost:8888", tags=["config:tag"])
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_instance = _mock_client()
            mock_cls.return_value = mock_instance
            mock_instance.aretain.return_value = _mock_retain_response()

            tools = create_hindsight_tools(bank_id="test")
            retain_tool = [t for t in tools if t.name == "retain_memory"][0]
            retain_tool.invoke(content="content")
            call_kwargs = mock_instance.aretain.call_args[1]
            assert call_kwargs["tags"] == ["config:tag"]

    def test_context_falls_back_to_config(self):
        configure(hindsight_api_url="http://localhost:8888", context="my-app")
        with patch("hindsight_haystack._client.Hindsight") as mock_cls:
            mock_instance = _mock_client()
            mock_cls.return_value = mock_instance
            mock_instance.aretain.return_value = _mock_retain_response()

            tools = create_hindsight_tools(bank_id="test")
            retain_tool = [t for t in tools if t.name == "retain_memory"][0]
            retain_tool.invoke(content="content")
            call_kwargs = mock_instance.aretain.call_args[1]
            assert call_kwargs["context"] == "my-app"
