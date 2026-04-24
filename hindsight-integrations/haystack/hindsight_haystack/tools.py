"""Haystack tool factory for Hindsight memory operations.

Provides a convenience factory that creates Haystack-compatible ``Tool``
instances backed by Hindsight's retain/recall/reflect APIs.
"""

import asyncio
import concurrent.futures
import logging
import uuid
from typing import Any, Optional

from haystack.tools import Tool
from hindsight_client import Hindsight

from ._client import resolve_client
from .config import get_config

logger = logging.getLogger(__name__)


def _run_sync(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine synchronously, even inside a running event loop.

    The hindsight_client's built-in sync methods (retain, recall, reflect) use
    ``loop.run_until_complete()`` internally, which raises when called from
    within an already-running event loop — e.g., inside Haystack's agent
    runtime.  This helper detects that situation and offloads the coroutine
    to a fresh thread with its own event loop.
    """
    try:
        asyncio.get_running_loop()
        # We're inside a running loop — run in a worker thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No running loop — safe to use asyncio.run directly.
        return asyncio.run(coro)


class _HindsightToolBackend:
    """Internal backend that implements Hindsight memory operations.

    Encapsulates client resolution, config fallback, bank management,
    and the retain/recall/reflect logic. Methods are wrapped as Haystack
    ``Tool`` objects by ``create_hindsight_tools()``.
    """

    def __init__(
        self,
        *,
        bank_id: str,
        client: Optional[Hindsight] = None,
        hindsight_api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        budget: Optional[str] = None,
        max_tokens: Optional[int] = None,
        tags: Optional[list[str]] = None,
        recall_tags: Optional[list[str]] = None,
        recall_tags_match: Optional[str] = None,
        # Retain options
        retain_metadata: Optional[dict[str, str]] = None,
        retain_document_id: Optional[str] = None,
        retain_context: Optional[str] = None,
        # Recall options
        recall_types: Optional[list[str]] = None,
        recall_include_entities: bool = False,
        # Reflect options
        reflect_context: Optional[str] = None,
        reflect_max_tokens: Optional[int] = None,
        reflect_response_schema: Optional[dict[str, Any]] = None,
        reflect_tags: Optional[list[str]] = None,
        reflect_tags_match: Optional[str] = None,
        # Bank management
        mission: Optional[str] = None,
    ):
        self._client = resolve_client(client, hindsight_api_url, api_key)
        self._bank_id = bank_id
        self._session_id = str(uuid.uuid4())[:8]
        self._bank_initialized = False

        # Resolve effective values using None-sentinel config fallback
        config = get_config()
        self._tags = tags if tags is not None else (config.tags if config else None)
        self._recall_tags = recall_tags if recall_tags is not None else (config.recall_tags if config else None)
        self._recall_tags_match = (
            recall_tags_match if recall_tags_match is not None else (config.recall_tags_match if config else "any")
        )
        self._budget = budget if budget is not None else (config.budget if config else "mid")
        self._max_tokens = max_tokens if max_tokens is not None else (config.max_tokens if config else 4096)

        # Retain-specific
        self._retain_metadata = retain_metadata
        self._retain_document_id = retain_document_id
        self._retain_context = (
            retain_context if retain_context is not None else (config.context if config else "haystack")
        )

        # Recall-specific
        self._recall_types = recall_types
        self._recall_include_entities = recall_include_entities

        # Reflect-specific
        self._reflect_context = reflect_context
        self._reflect_max_tokens = reflect_max_tokens
        self._reflect_response_schema = reflect_response_schema
        self._reflect_tags = reflect_tags
        self._reflect_tags_match = reflect_tags_match

        # Bank management
        self._mission = mission if mission is not None else (config.mission if config else None)

    def _ensure_bank(self) -> None:
        """Create/update the bank with mission if not already done."""
        if self._bank_initialized or not self._mission:
            return
        try:
            _run_sync(
                self._client.acreate_bank(
                    bank_id=self._bank_id,
                    name=self._bank_id,
                    mission=self._mission,
                )
            )
            self._bank_initialized = True
            logger.debug(f"Created/updated bank: {self._bank_id}")
        except Exception as e:
            # Bank may already exist — that's fine
            self._bank_initialized = True
            logger.debug(f"Bank creation for {self._bank_id}: {e}")

    def _generate_document_id(self) -> str:
        """Generate a unique document_id for retain operations."""
        return f"{self._session_id}-{uuid.uuid4().hex[:12]}"

    def _retain_kwargs(self, content: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "bank_id": self._bank_id,
            "content": content,
            "context": self._retain_context,
        }
        if self._tags:
            kwargs["tags"] = self._tags
        if self._retain_metadata:
            kwargs["metadata"] = self._retain_metadata
        # Use explicit document_id if set, otherwise auto-generate
        kwargs["document_id"] = self._retain_document_id or self._generate_document_id()
        return kwargs

    def _recall_kwargs(self, query: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "bank_id": self._bank_id,
            "query": query,
            "budget": self._budget,
            "max_tokens": self._max_tokens,
        }
        if self._recall_tags:
            kwargs["tags"] = self._recall_tags
            kwargs["tags_match"] = self._recall_tags_match
        if self._recall_types:
            kwargs["types"] = self._recall_types
        if self._recall_include_entities:
            kwargs["include_entities"] = True
        return kwargs

    def _reflect_kwargs(self, query: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "bank_id": self._bank_id,
            "query": query,
            "budget": self._budget,
        }
        if self._reflect_context:
            kwargs["context"] = self._reflect_context
        effective_reflect_max = self._reflect_max_tokens or self._max_tokens
        if effective_reflect_max:
            kwargs["max_tokens"] = effective_reflect_max
        if self._reflect_response_schema:
            kwargs["response_schema"] = self._reflect_response_schema
        effective_reflect_tags = self._reflect_tags if self._reflect_tags is not None else self._recall_tags
        effective_reflect_tags_match = self._reflect_tags_match or self._recall_tags_match
        if effective_reflect_tags:
            kwargs["tags"] = effective_reflect_tags
            kwargs["tags_match"] = effective_reflect_tags_match
        return kwargs

    @staticmethod
    def _format_recall(response: Any) -> str:
        if not response.results:
            return "No relevant memories found."
        lines = []
        for i, result in enumerate(response.results, 1):
            lines.append(f"{i}. {result.text}")
        return "\n".join(lines)

    def retain_memory(self, content: str) -> str:
        """Store information to long-term memory for later retrieval.

        Use this to save important facts, user preferences, decisions,
        or any information that should be remembered across conversations.

        Args:
            content: The information to store in memory.
        """
        try:
            self._ensure_bank()
            _run_sync(self._client.aretain(**self._retain_kwargs(content)))
            return "Memory stored successfully."
        except Exception as e:
            logger.error(f"Retain failed: {e}")
            return f"Failed to store memory: {e}"

    def recall_memory(self, query: str) -> str:
        """Search long-term memory for relevant information.

        Use this to find previously stored facts, preferences, or context.
        Returns a numbered list of matching memories.

        Args:
            query: What to search for in memory.
        """
        try:
            self._ensure_bank()
            response = _run_sync(self._client.arecall(**self._recall_kwargs(query)))
            return self._format_recall(response)
        except Exception as e:
            logger.error(f"Recall failed: {e}")
            return f"Failed to search memory: {e}"

    def reflect_on_memory(self, query: str) -> str:
        """Synthesize a thoughtful answer from long-term memories.

        Use this when you need a coherent summary or reasoned response
        about what you know, rather than raw memory facts.

        Args:
            query: The question to reflect on using stored memories.
        """
        try:
            self._ensure_bank()
            response = _run_sync(self._client.areflect(**self._reflect_kwargs(query)))
            return response.text or "No relevant memories found."
        except Exception as e:
            logger.error(f"Reflect failed: {e}")
            return f"Failed to reflect on memory: {e}"


# -- Tool definitions mapping operation name → (method name, description, parameter schema) --
_TOOL_DEFS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "retain_memory": (
        "retain_memory",
        (
            "Store information to long-term memory for later retrieval. "
            "Use this to save important facts, user preferences, decisions, "
            "or any information that should be remembered across conversations."
        ),
        {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to store in memory.",
                },
            },
            "required": ["content"],
        },
    ),
    "recall_memory": (
        "recall_memory",
        (
            "Search long-term memory for relevant information. "
            "Use this to find previously stored facts, preferences, or context. "
            "Returns a numbered list of matching memories."
        ),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memory.",
                },
            },
            "required": ["query"],
        },
    ),
    "reflect_on_memory": (
        "reflect_on_memory",
        (
            "Synthesize a thoughtful answer from long-term memories. "
            "Use this when you need a coherent summary or reasoned response "
            "about what you know, rather than raw memory facts."
        ),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The question to reflect on using stored memories.",
                },
            },
            "required": ["query"],
        },
    ),
}


class _HindsightTool(Tool):
    """A Haystack Tool backed by a Hindsight memory operation.

    Overrides ``to_dict()``/``from_dict()`` so that the tool's configuration
    (bank_id, API URL, etc.) is serialized instead of the bound method,
    which Haystack cannot serialize.
    """

    def __init__(
        self,
        *,
        backend: "_HindsightToolBackend",
        tool_name: str,
        backend_kwargs: dict[str, Any],
    ):
        method_name, description, parameters = _TOOL_DEFS[tool_name]
        super().__init__(
            name=tool_name,
            description=description,
            function=getattr(backend, method_name),
            parameters=parameters,
        )
        self._backend = backend
        self._backend_kwargs = backend_kwargs

    def to_dict(self) -> dict[str, Any]:
        """Serialize the tool to a dictionary.

        Stores the backend configuration so the tool can be reconstructed
        via ``from_dict()`` without needing to serialize the bound method.
        """
        data = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "backend_kwargs": self._backend_kwargs,
        }
        cls = type(self)
        qualified_name = f"{cls.__module__}.{cls.__qualname__}"
        return {"type": qualified_name, "data": data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_HindsightTool":
        """Deserialize the tool from a dictionary."""
        inner = data["data"]
        backend_kwargs = inner["backend_kwargs"]
        backend = _HindsightToolBackend(**backend_kwargs)
        return cls(
            backend=backend,
            tool_name=inner["name"],
            backend_kwargs=backend_kwargs,
        )


def create_hindsight_tools(
    *,
    bank_id: str,
    client: Optional[Hindsight] = None,
    hindsight_api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    budget: Optional[str] = None,
    max_tokens: Optional[int] = None,
    tags: Optional[list[str]] = None,
    recall_tags: Optional[list[str]] = None,
    recall_tags_match: Optional[str] = None,
    # Retain options
    retain_metadata: Optional[dict[str, str]] = None,
    retain_document_id: Optional[str] = None,
    retain_context: Optional[str] = None,
    # Recall options
    recall_types: Optional[list[str]] = None,
    recall_include_entities: bool = False,
    # Reflect options
    reflect_context: Optional[str] = None,
    reflect_max_tokens: Optional[int] = None,
    reflect_response_schema: Optional[dict[str, Any]] = None,
    reflect_tags: Optional[list[str]] = None,
    reflect_tags_match: Optional[str] = None,
    # Bank management
    mission: Optional[str] = None,
    include_retain: bool = True,
    include_recall: bool = True,
    include_reflect: bool = True,
) -> list[Tool]:
    """Create Hindsight memory tools for a Haystack agent.

    Convenience factory that creates a backend and returns Haystack ``Tool``
    instances ready for use with any Haystack agent.

    Args:
        bank_id: The Hindsight memory bank to operate on.
        client: Pre-configured Hindsight client (preferred).
        hindsight_api_url: API URL (used if no client provided).
        api_key: API key (used if no client provided).
        budget: Recall/reflect budget level (low/mid/high).
        max_tokens: Maximum tokens for recall results.
        tags: Tags applied when storing memories via retain.
        recall_tags: Tags to filter when searching memories.
        recall_tags_match: Tag matching mode (any/all/any_strict/all_strict).
        retain_metadata: Default metadata dict for retain operations.
        retain_document_id: Default document_id for retain. If None,
            auto-generates per call.
        retain_context: Source label for retain operations.
        recall_types: Fact types to filter (world, experience, opinion, observation).
        recall_include_entities: Include entity information in recall results.
        reflect_context: Additional context for reflect operations.
        reflect_max_tokens: Max tokens for reflect results (defaults to max_tokens).
        reflect_response_schema: JSON schema to constrain reflect output format.
        reflect_tags: Tags to filter memories used in reflect (defaults to recall_tags).
        reflect_tags_match: Tag matching for reflect (defaults to recall_tags_match).
        mission: Bank mission for fact extraction context.
        include_retain: Include the retain (store) tool.
        include_recall: Include the recall (search) tool.
        include_reflect: Include the reflect (synthesize) tool.

    Returns:
        List of Haystack Tool instances.

    Raises:
        HindsightError: If no client or API URL can be resolved.
    """
    # Build kwargs dict for backend (used for both instantiation and serialization).
    # The client object can't be serialized, so extract its connection info
    # into backend_kwargs to enable round-trip serialization.
    serializable_url = hindsight_api_url
    serializable_key = api_key
    if client is not None and serializable_url is None:
        serializable_url = getattr(client, "_base_url", None) or getattr(client, "base_url", None)
        if serializable_url is not None:
            serializable_url = str(serializable_url)
    if client is not None and serializable_key is None:
        serializable_key = getattr(client, "_api_key", None) or getattr(client, "api_key", None)

    backend_kwargs: dict[str, Any] = {
        "bank_id": bank_id,
        "hindsight_api_url": serializable_url,
        "api_key": serializable_key,
        "budget": budget,
        "max_tokens": max_tokens,
        "tags": tags,
        "recall_tags": recall_tags,
        "recall_tags_match": recall_tags_match,
        "retain_metadata": retain_metadata,
        "retain_document_id": retain_document_id,
        "retain_context": retain_context,
        "recall_types": recall_types,
        "recall_include_entities": recall_include_entities,
        "reflect_context": reflect_context,
        "reflect_max_tokens": reflect_max_tokens,
        "reflect_response_schema": reflect_response_schema,
        "reflect_tags": reflect_tags,
        "reflect_tags_match": reflect_tags_match,
        "mission": mission,
    }

    backend = _HindsightToolBackend(client=client, **backend_kwargs)

    tools: list[Tool] = []

    if include_retain:
        tools.append(_HindsightTool(backend=backend, tool_name="retain_memory", backend_kwargs=backend_kwargs))

    if include_recall:
        tools.append(_HindsightTool(backend=backend, tool_name="recall_memory", backend_kwargs=backend_kwargs))

    if include_reflect:
        tools.append(_HindsightTool(backend=backend, tool_name="reflect_on_memory", backend_kwargs=backend_kwargs))

    return tools
