"""Tool registry — register, look-up, validate, and execute tools.

The registry is the single source of truth for which tools are available
in the current session.  It also owns the shared :class:`ToolContext`.
"""

from __future__ import annotations

import re
from typing import Any

from claude_code.tools.base import Tool, ToolContext, ToolResult


class ToolRegistryError(Exception):
    """Raised when a tool cannot be found or validated."""


class ToolRegistry:
    """Central registry that holds tool instances and dispatches calls.

    Example::

        registry = ToolRegistry()
        registry.register(ReadTool())
        result = await registry.execute("Read", {"file_path": "/tmp/x.py"})
    """

    def __init__(self, context: ToolContext | None = None) -> None:
        self.context = context or ToolContext()
        self._tools: dict[str, Tool] = {}

    # -- registration ------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Add *tool* to the registry, replacing any existing tool with the same name."""
        tool.context = self.context
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove the tool called *name*.  Raises if not found."""
        if name not in self._tools:
            raise ToolRegistryError(f"Tool '{name}' is not registered")
        del self._tools[name]

    def get(self, name: str) -> Tool:
        """Return the tool instance for *name*."""
        if name not in self._tools:
            raise ToolRegistryError(f"Tool '{name}' is not registered")
        return self._tools[name]

    def list_all(self) -> list[Tool]:
        """Return all registered tools in registration order."""
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        """Return the names of all registered tools."""
        return list(self._tools.keys())

    # -- API helpers -------------------------------------------------------

    def get_definitions(self) -> list[dict[str, Any]]:
        """Return tool definitions suitable for the LLM ``tools`` parameter."""
        return [tool.get_definition() for tool in self._tools.values()]

    # -- execution ---------------------------------------------------------

    async def execute(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Validate *params* against the tool schema and execute.

        Returns a :class:`ToolResult`.  Validation errors are returned as
        error results rather than raised, so the caller can forward them
        to the model.
        """
        try:
            tool = self.get(name)
        except ToolRegistryError as exc:
            return ToolResult.error(str(exc))

        # Validate
        errors = _validate_params(tool.input_schema, params)
        if errors:
            return ToolResult.error(
                f"Invalid parameters for tool '{name}': " + "; ".join(errors)
            )

        try:
            return await tool.execute(**params)
        except Exception as exc:  # noqa: BLE001 — tool errors become results
            return ToolResult.error(f"Tool '{name}' failed: {exc}")


# ---------------------------------------------------------------------------
# Lightweight JSON-Schema validator (subset used by tool input_schema)
# ---------------------------------------------------------------------------

def _validate_params(schema: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """Validate *params* against a JSON-Schema-like *schema*.

    Supports the subset of JSON Schema used by tool definitions:
    ``type``, ``required``, ``properties``, ``enum``, ``minimum``,
    ``maximum``, ``minLength``, ``maxLength``, ``pattern``, ``items``.

    Returns a list of human-readable error strings (empty == valid).
    """
    errors: list[str] = []

    schema_type = schema.get("type")
    if schema_type and schema_type != "object":
        # Top-level tool schemas are always objects.
        return [f"Expected schema type 'object', got '{schema_type}'"]

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    # Check required fields
    for key in required:
        if key not in params:
            errors.append(f"Missing required parameter '{key}'")

    # Check each provided parameter
    for key, value in params.items():
        if key not in properties:
            # additionalProperties is usually false in tool schemas
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(f"Unknown parameter '{key}'")
            continue

        prop_schema = properties[key]
        prop_errors = _validate_value(prop_schema, value, key)
        errors.extend(prop_errors)

    return errors


def _validate_value(prop_schema: dict[str, Any], value: Any, path: str) -> list[str]:
    """Validate a single value against its property schema."""
    errors: list[str] = []
    expected_type = prop_schema.get("type")

    if expected_type and not _check_type(expected_type, value):
        errors.append(
            f"Parameter '{path}' expected type '{expected_type}', "
            f"got '{type(value).__name__}'"
        )
        return errors  # no point checking further constraints

    # enum
    if "enum" in prop_schema and value not in prop_schema["enum"]:
        errors.append(
            f"Parameter '{path}' must be one of {prop_schema['enum']}, got {value!r}"
        )

    # numeric constraints
    if isinstance(value, (int, float)):
        if "minimum" in prop_schema and value < prop_schema["minimum"]:
            errors.append(
                f"Parameter '{path}' must be >= {prop_schema['minimum']}, got {value}"
            )
        if "maximum" in prop_schema and value > prop_schema["maximum"]:
            errors.append(
                f"Parameter '{path}' must be <= {prop_schema['maximum']}, got {value}"
            )

    # string constraints
    if isinstance(value, str):
        if "minLength" in prop_schema and len(value) < prop_schema["minLength"]:
            errors.append(
                f"Parameter '{path}' must have length >= {prop_schema['minLength']}"
            )
        if "maxLength" in prop_schema and len(value) > prop_schema["maxLength"]:
            errors.append(
                f"Parameter '{path}' must have length <= {prop_schema['maxLength']}"
            )
        if "pattern" in prop_schema:
            if not re.search(prop_schema["pattern"], value):
                errors.append(
                    f"Parameter '{path}' does not match pattern "
                    f"'{prop_schema['pattern']}'"
                )

    # array items
    if isinstance(value, list) and "items" in prop_schema:
        for i, item in enumerate(value):
            item_errors = _validate_value(
                prop_schema["items"], item, f"{path}[{i}]"
            )
            errors.extend(item_errors)

    return errors


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


def _check_type(expected: str | list[str], value: Any) -> bool:
    """Check whether *value* matches the JSON-Schema *expected* type(s)."""
    if isinstance(expected, list):
        return any(_check_type(t, value) for t in expected)
    types = _TYPE_MAP.get(expected)
    if types is None:
        return True  # unknown type → accept
    # int is also valid "number"
    return isinstance(value, types)
