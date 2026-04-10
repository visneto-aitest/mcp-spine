"""
MCP Spine — Schema Minifier (Stage 3)

Progressively strips JSON-RPC tool schemas to reduce token count
while preserving the LLM's ability to use the tools correctly.

Minification levels:
  0 = Off (passthrough)
  1 = Light — remove metadata ($schema, examples, defaults, titles)
  2 = Standard — Level 1 + remove param descriptions, collapse types
  3 = Aggressive — Level 2 + flatten shallow nested objects

Typical savings:
  Level 1: ~25% token reduction
  Level 2: ~55% token reduction
  Level 3: ~65% token reduction
"""

from __future__ import annotations

import copy
import json
from typing import Any


class SchemaMinifier:
    """
    Progressive JSON schema minifier for MCP tool schemas.

    Strips unnecessary metadata from tool schemas to save LLM tokens
    while preserving the structural information needed for correct usage.
    """

    # Keys stripped at Level 1
    _METADATA_KEYS = frozenset({
        "$schema", "$id", "$comment", "title",
        "examples", "default", "additionalProperties",
        "readOnly", "writeOnly", "deprecated",
        "externalDocs", "xml",
    })

    # Keys stripped at Level 2 (within properties)
    _PARAM_DETAIL_KEYS = frozenset({
        "description", "examples", "default",
        "title", "$comment", "readOnly", "writeOnly",
    })

    def __init__(
        self,
        level: int = 2,
        max_description_length: int = 120,
        preserve_required: bool = True,
    ):
        if level not in range(4):
            raise ValueError(f"Minification level must be 0-3, got {level}")
        self.level = level
        self.max_desc_length = max_description_length
        self.preserve_required = preserve_required

    def minify(self, tool: dict[str, Any]) -> dict[str, Any]:
        """
        Minify a single tool schema.

        Returns a new dict (does not modify the original).
        """
        if self.level == 0:
            return tool

        result: dict[str, Any] = {"name": tool["name"]}

        # Always keep description (shortened)
        if desc := tool.get("description", ""):
            result["description"] = self._shorten_description(desc)

        # Minify input schema
        if "inputSchema" in tool:
            result["inputSchema"] = self._minify_schema(
                copy.deepcopy(tool["inputSchema"])
            )

        return result

    def minify_batch(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Minify a list of tool schemas."""
        return [self.minify(t) for t in tools]

    def _shorten_description(self, desc: str) -> str:
        """Truncate description to first sentence or max chars."""
        # Take first sentence
        for sep in (". ", ".\n", ".\t"):
            idx = desc.find(sep)
            if idx != -1:
                first = desc[:idx + 1]
                if len(first) <= self.max_desc_length:
                    return first
                break

        # If first sentence is too long or no sentence boundary found
        if len(desc) <= self.max_desc_length:
            return desc

        # Truncate at word boundary
        truncated = desc[:self.max_desc_length]
        last_space = truncated.rfind(" ")
        if last_space > self.max_desc_length * 0.5:
            return truncated[:last_space] + "..."
        return truncated + "..."

    def _minify_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Apply minification rules to a JSON schema."""
        # Level 1: Strip metadata keys
        schema = self._strip_keys(schema, self._METADATA_KEYS)

        if self.level >= 2:
            # Strip descriptions from individual parameters
            schema = self._strip_param_details(schema)
            # Collapse trivial type wrappers
            schema = self._collapse_simple_types(schema)

        if self.level >= 3:
            # Flatten shallow nested objects
            schema = self._flatten_shallow(schema)

        return schema

    def _strip_keys(
        self, obj: dict[str, Any], keys: frozenset[str]
    ) -> dict[str, Any]:
        """Recursively remove specified keys from a schema."""
        if not isinstance(obj, dict):
            return obj

        result = {}
        for k, v in obj.items():
            if k in keys:
                continue
            if isinstance(v, dict):
                result[k] = self._strip_keys(v, keys)
            elif isinstance(v, list):
                result[k] = [
                    self._strip_keys(item, keys)
                    if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                result[k] = v
        return result

    def _strip_param_details(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Remove descriptions and details from parameter definitions."""
        props = schema.get("properties", {})
        for key, prop in props.items():
            if isinstance(prop, dict):
                for detail_key in self._PARAM_DETAIL_KEYS:
                    prop.pop(detail_key, None)

                # Recurse into nested objects
                if prop.get("type") == "object" and "properties" in prop:
                    self._strip_param_details(prop)

                # Handle array items
                if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
                    items = prop["items"]
                    for detail_key in self._PARAM_DETAIL_KEYS:
                        items.pop(detail_key, None)
                    if items.get("type") == "object" and "properties" in items:
                        self._strip_param_details(items)

        return schema

    def _collapse_simple_types(self, schema: dict[str, Any]) -> dict[str, Any]:
        """
        Collapse trivial type definitions.

        {"type": "string"} can remain as-is since it's already minimal.
        But {"type": "string", "minLength": 1} stays expanded.
        Remove empty enum arrays and single-option enums.
        """
        props = schema.get("properties", {})
        for key, prop in list(props.items()):
            if not isinstance(prop, dict):
                continue

            # If only "type" remains, it's already minimal
            # Handle single-value enums: {"enum": ["value"]} → keep for now

            # Remove empty constraints
            for constraint in ("minLength", "maxLength", "minimum", "maximum", "pattern"):
                if constraint in prop and prop[constraint] is None:
                    del prop[constraint]

            # Collapse anyOf with null (optional types)
            if "anyOf" in prop and len(prop["anyOf"]) == 2:
                types = prop["anyOf"]
                non_null = [t for t in types if t != {"type": "null"}]
                if len(non_null) == 1 and isinstance(non_null[0], dict):
                    # Replace anyOf with the non-null type
                    actual = non_null[0]
                    del prop["anyOf"]
                    prop.update(actual)

        return schema

    def _flatten_shallow(self, schema: dict[str, Any]) -> dict[str, Any]:
        """
        Level 3: Flatten single-level nested objects with few properties.

        If a parameter is an object with <= 2 simple properties,
        promote those properties to the parent level with dotted names.
        """
        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        flattened_props = {}
        new_required = set()

        for key, prop in props.items():
            if not isinstance(prop, dict):
                flattened_props[key] = prop
                if key in required:
                    new_required.add(key)
                continue

            # Only flatten simple nested objects
            if (
                prop.get("type") == "object"
                and "properties" in prop
                and len(prop["properties"]) <= 2
                and all(
                    isinstance(v, dict) and v.get("type") in ("string", "number", "integer", "boolean")
                    for v in prop["properties"].values()
                )
            ):
                # Promote nested properties with dotted names
                nested_required = set(prop.get("required", []))
                for nkey, nval in prop["properties"].items():
                    flat_key = f"{key}.{nkey}"
                    flattened_props[flat_key] = nval
                    if key in required and nkey in nested_required:
                        new_required.add(flat_key)
            else:
                flattened_props[key] = prop
                if key in required:
                    new_required.add(key)

        schema["properties"] = flattened_props
        if self.preserve_required and new_required:
            schema["required"] = sorted(new_required)
        elif "required" in schema and not new_required:
            del schema["required"]

        return schema

    def estimate_tokens(self, tool: dict[str, Any]) -> int:
        """Rough token estimate for a tool schema (~4 chars per token)."""
        text = json.dumps(tool, separators=(",", ":"))
        return len(text) // 4

    def compare(self, tool: dict[str, Any]) -> dict[str, Any]:
        """
        Compare original vs minified schema sizes.

        Returns stats dict with token counts and savings.
        """
        original_tokens = self.estimate_tokens(tool)
        minified = self.minify(tool)
        minified_tokens = self.estimate_tokens(minified)

        savings = original_tokens - minified_tokens
        pct = (savings / original_tokens * 100) if original_tokens > 0 else 0

        return {
            "tool_name": tool["name"],
            "original_tokens": original_tokens,
            "minified_tokens": minified_tokens,
            "savings_tokens": savings,
            "savings_pct": round(pct, 1),
            "level": self.level,
        }
