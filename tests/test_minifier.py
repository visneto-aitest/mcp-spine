"""Tests for the Schema Minifier (Stage 3)."""

import pytest
from spine.minifier import SchemaMinifier


SAMPLE_TOOL = {
    "name": "create_pull_request",
    "description": (
        "Create a new pull request in a GitHub repository. "
        "This tool allows you to create a new pull request with a title, "
        "description, and specify the source and target branches. "
        "You can also mark it as a draft pull request."
    ),
    "inputSchema": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": "CreatePullRequestInput",
        "additionalProperties": False,
        "properties": {
            "owner": {
                "type": "string",
                "description": "The owner of the repository (user or organization)",
            },
            "repo": {
                "type": "string",
                "description": "The name of the repository",
            },
            "title": {
                "type": "string",
                "description": "The title of the pull request",
            },
            "body": {
                "type": "string",
                "description": "The description/body content of the pull request",
                "default": "",
            },
            "head": {
                "type": "string",
                "description": "The branch with your changes",
            },
            "base": {
                "type": "string",
                "description": "The branch you want changes pulled into",
            },
            "draft": {
                "type": "boolean",
                "description": "Whether to create as a draft",
                "default": False,
            },
        },
        "required": ["owner", "repo", "title", "head", "base"],
    },
}


class TestMinifierLevel0:
    def test_passthrough(self):
        m = SchemaMinifier(level=0)
        result = m.minify(SAMPLE_TOOL)
        assert result == SAMPLE_TOOL


class TestMinifierLevel1:
    def test_strips_metadata(self):
        m = SchemaMinifier(level=1)
        result = m.minify(SAMPLE_TOOL)
        schema = result["inputSchema"]
        assert "$schema" not in schema
        assert "title" not in schema
        assert "additionalProperties" not in schema

    def test_keeps_description(self):
        m = SchemaMinifier(level=1)
        result = m.minify(SAMPLE_TOOL)
        assert "description" in result

    def test_keeps_properties(self):
        m = SchemaMinifier(level=1)
        result = m.minify(SAMPLE_TOOL)
        props = result["inputSchema"]["properties"]
        assert "owner" in props
        assert "repo" in props

    def test_keeps_required(self):
        m = SchemaMinifier(level=1)
        result = m.minify(SAMPLE_TOOL)
        assert "required" in result["inputSchema"]


class TestMinifierLevel2:
    def test_strips_param_descriptions(self):
        m = SchemaMinifier(level=2)
        result = m.minify(SAMPLE_TOOL)
        for prop in result["inputSchema"]["properties"].values():
            assert "description" not in prop

    def test_strips_defaults(self):
        m = SchemaMinifier(level=2)
        result = m.minify(SAMPLE_TOOL)
        for prop in result["inputSchema"]["properties"].values():
            assert "default" not in prop

    def test_shortens_description(self):
        m = SchemaMinifier(level=2, max_description_length=50)
        result = m.minify(SAMPLE_TOOL)
        assert len(result["description"]) <= 53  # +3 for "..."

    def test_keeps_types(self):
        m = SchemaMinifier(level=2)
        result = m.minify(SAMPLE_TOOL)
        props = result["inputSchema"]["properties"]
        assert props["owner"]["type"] == "string"
        assert props["draft"]["type"] == "boolean"


class TestMinifierLevel3:
    def test_flattens_shallow_nested(self):
        tool = {
            "name": "test_tool",
            "description": "Test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "port": {"type": "integer"},
                        },
                        "required": ["host"],
                    },
                    "name": {"type": "string"},
                },
                "required": ["config"],
            },
        }
        m = SchemaMinifier(level=3)
        result = m.minify(tool)
        props = result["inputSchema"]["properties"]
        assert "config.host" in props
        assert "config.port" in props
        assert "config" not in props
        assert "name" in props

    def test_does_not_flatten_deep_nested(self):
        tool = {
            "name": "test_tool",
            "description": "Test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "string"},
                            "b": {"type": "string"},
                            "c": {"type": "string"},
                        },
                    },
                },
            },
        }
        m = SchemaMinifier(level=3)
        result = m.minify(tool)
        # 3 properties — should NOT flatten
        assert "config" in result["inputSchema"]["properties"]


class TestMinifierTokenSavings:
    def test_significant_savings_at_level_2(self):
        m = SchemaMinifier(level=2)
        stats = m.compare(SAMPLE_TOOL)
        assert stats["savings_pct"] > 40  # at least 40% savings

    def test_batch_minification(self):
        m = SchemaMinifier(level=2)
        tools = [SAMPLE_TOOL, SAMPLE_TOOL]
        results = m.minify_batch(tools)
        assert len(results) == 2
        assert all("name" in t for t in results)

    def test_no_original_mutation(self):
        m = SchemaMinifier(level=2)
        import copy
        original = copy.deepcopy(SAMPLE_TOOL)
        m.minify(SAMPLE_TOOL)
        assert SAMPLE_TOOL == original
