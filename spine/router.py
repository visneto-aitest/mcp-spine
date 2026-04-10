"""
MCP Spine — Semantic Router (Stage 2)

Routes user intent to the most relevant tools using local vector
embeddings. No API calls, no cloud services.

Flow:
  1. On startup, embed all tool schemas into ChromaDB
  2. On each tools/list request, query the embedding space
  3. Return only the top-K most relevant tools
  4. Optional reranking: keyword overlap + recency boost

Dependencies (install with: pip install mcp-spine[ml]):
  - sentence-transformers (all-MiniLM-L6-v2, ~80MB)
  - chromadb (local SQLite-backed vector store)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spine.audit import AuditLogger, EventType


def _tool_to_text(tool: dict[str, Any]) -> str:
    """
    Create a rich text representation of a tool for embedding.

    Includes name, description, and parameter names/descriptions
    to give the embedding model maximum semantic surface area.
    """
    parts = [
        f"Tool: {tool['name']}",
    ]
    if desc := tool.get("description", ""):
        parts.append(f"Purpose: {desc}")

    schema = tool.get("inputSchema", {})
    props = schema.get("properties", {})
    if props:
        param_parts = []
        for key, val in props.items():
            param_desc = val.get("description", key)
            param_parts.append(f"{key}: {param_desc}")
        parts.append("Parameters: " + "; ".join(param_parts))

    return "\n".join(parts)


def _tool_hash(tool: dict[str, Any]) -> str:
    """Deterministic hash for change detection."""
    canonical = json.dumps(tool, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class RouteResult:
    """Result of a semantic routing query."""
    tool_name: str
    distance: float
    server_name: str | None = None


class SemanticRouter:
    """
    Semantic tool router using local embeddings.

    Embeds tool schemas into ChromaDB and queries them
    against user intent to find the most relevant tools.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        persist_dir: str = ".spine/chroma",
        collection_name: str = "tool_schemas",
        max_tools: int = 5,
        similarity_threshold: float = 0.3,
        always_include: list[str] | None = None,
        rerank: bool = True,
        logger: AuditLogger | None = None,
    ):
        self._model_name = model_name
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        self._max_tools = max_tools
        self._similarity_threshold = similarity_threshold
        self._always_include = set(always_include or [])
        self._rerank = rerank
        self._logger = logger

        # Lazy-loaded
        self._model = None
        self._collection = None
        self._client = None

        # State
        self._all_tools: dict[str, dict] = {}
        self._tool_hashes: dict[str, str] = {}
        self._recent_calls: list[str] = []  # last N tool calls for recency boost
        self._max_recent = 20

    def _ensure_loaded(self) -> None:
        """Lazy-load the embedding model and ChromaDB on first use."""
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer
            import chromadb
        except ImportError:
            raise ImportError(
                "Semantic routing requires ML dependencies. "
                "Install with: pip install mcp-spine[ml]"
            )

        self._model = SentenceTransformer(self._model_name)

        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def index_tools(self, tools: list[dict[str, Any]]) -> int:
        """
        Index (or re-index) tool schemas into the vector store.

        Only re-embeds tools whose schemas have changed.
        Returns the number of tools that were (re-)embedded.
        """
        self._ensure_loaded()

        embedded_count = 0
        current_ids = set()

        for tool in tools:
            name = tool["name"]
            current_ids.add(name)
            self._all_tools[name] = tool

            # Check if schema changed
            new_hash = _tool_hash(tool)
            if self._tool_hashes.get(name) == new_hash:
                continue  # unchanged, skip re-embedding

            self._tool_hashes[name] = new_hash
            text = _tool_to_text(tool)

            # Upsert into ChromaDB
            self._collection.upsert(
                ids=[name],
                documents=[text],
                metadatas=[{
                    "tool_name": name,
                    "server": tool.get("_spine_server", "unknown"),
                    "schema_hash": new_hash,
                }],
            )
            embedded_count += 1

        # Remove tools that no longer exist
        existing_ids = set()
        try:
            result = self._collection.get()
            existing_ids = set(result["ids"])
        except Exception:
            pass

        stale = existing_ids - current_ids
        if stale:
            self._collection.delete(ids=list(stale))
            for name in stale:
                self._all_tools.pop(name, None)
                self._tool_hashes.pop(name, None)

        if self._logger:
            self._logger.info(
                EventType.TOOL_ROUTED,
                action="index",
                total=len(tools),
                embedded=embedded_count,
                removed=len(stale),
            )

        return embedded_count

    def route(
        self,
        query: str,
        available_tools: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find the most relevant tools for a given query.

        Args:
            query: The user's intent (prompt text or context hint)
            available_tools: If provided, filter results to only these tools.
                             If None, uses all indexed tools.

        Returns:
            List of tool dicts, ordered by relevance, capped at max_tools.
        """
        self._ensure_loaded()

        if not query or not query.strip():
            # No context — return all tools (graceful degradation)
            tools = available_tools or list(self._all_tools.values())
            return tools[:self._max_tools]

        # Query ChromaDB
        n_results = min(
            self._max_tools * 2,  # fetch extra for reranking
            self._collection.count(),
        )

        if n_results == 0:
            return available_tools or []

        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
        )

        if not results["ids"] or not results["ids"][0]:
            return available_tools or []

        # Build candidate list with distances
        candidates: list[RouteResult] = []
        ids = results["ids"][0]
        distances = results["distances"][0] if results.get("distances") else [0.0] * len(ids)
        metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(ids)

        available_names = (
            {t["name"] for t in available_tools} if available_tools else None
        )

        for tool_id, dist, meta in zip(ids, distances, metadatas):
            # Filter to available tools if specified
            if available_names and tool_id not in available_names:
                continue
            # Apply similarity threshold (cosine distance: lower = more similar)
            if dist > (1.0 - self._similarity_threshold):
                continue
            candidates.append(RouteResult(
                tool_name=tool_id,
                distance=dist,
                server_name=meta.get("server"),
            ))

        # Rerank if enabled
        if self._rerank and candidates:
            candidates = self._rerank_candidates(candidates, query)

        # Select top-K
        selected_names = set()

        # Always-include tools first
        result_tools: list[dict[str, Any]] = []
        for name in self._always_include:
            if name in self._all_tools:
                result_tools.append(self._all_tools[name])
                selected_names.add(name)

        # Add routed tools
        for candidate in candidates:
            if candidate.tool_name in selected_names:
                continue
            if candidate.tool_name in self._all_tools:
                result_tools.append(self._all_tools[candidate.tool_name])
                selected_names.add(candidate.tool_name)
            if len(result_tools) >= self._max_tools:
                break

        if self._logger:
            self._logger.info(
                EventType.TOOL_ROUTED,
                query=query[:100],
                candidates=len(candidates),
                selected=len(result_tools),
                tools=[t["name"] for t in result_tools],
            )

        return result_tools

    def _rerank_candidates(
        self,
        candidates: list[RouteResult],
        query: str,
    ) -> list[RouteResult]:
        """
        Lightweight reranking with keyword overlap and recency boost.

        Adjusts distances (lower = better) based on:
          - Exact keyword overlap between query and tool name
          - Recent usage of the tool (recency boost)
        """
        query_tokens = set(query.lower().split())

        scored = []
        for candidate in candidates:
            score = candidate.distance

            # Keyword overlap bonus
            name_tokens = set(
                candidate.tool_name.lower().replace("_", " ").replace("-", " ").split()
            )
            overlap = len(query_tokens & name_tokens)
            score -= overlap * 0.12  # each keyword match improves score

            # Recency boost
            if candidate.tool_name in self._recent_calls:
                recency_idx = self._recent_calls.index(candidate.tool_name)
                recency_boost = 0.08 * (1.0 - recency_idx / self._max_recent)
                score -= recency_boost

            scored.append((score, candidate))

        scored.sort(key=lambda x: x[0])
        return [c for _, c in scored]

    def record_tool_call(self, tool_name: str) -> None:
        """Record a tool call for recency-based reranking."""
        if tool_name in self._recent_calls:
            self._recent_calls.remove(tool_name)
        self._recent_calls.insert(0, tool_name)
        if len(self._recent_calls) > self._max_recent:
            self._recent_calls.pop()

    def set_context(self, context: str) -> list[dict[str, Any]]:
        """
        Explicit context-setting (called by spine_set_context tool).

        Returns the newly routed tool list.
        """
        return self.route(context)

    @property
    def indexed_count(self) -> int:
        """Number of tools currently indexed."""
        if self._collection is None:
            return 0
        return self._collection.count()
