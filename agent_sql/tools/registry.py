"""Tool registry: strict JSON schemas, argument validation, permissions (docs/spec/TOOLS.md).

Data tools count toward the tool-call budget; control tools (submit_plan, complete_step, submit_answer) do not.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_sql.llm.base import ToolSpec

S = {"type": "string"}
NS = {"type": ["string", "null"]}
PURPOSE = {"type": "string", "description": "Why you call this tool (1-200 chars). Shown to users."}


def obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": required if required is not None else list(props)}


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: dict
    fn: Callable[..., dict] | None  # None for control tools handled by the controller
    data_tool: bool = True

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.schema)


class ArgError(ValueError):
    pass


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict,
          "null": type(None)}


def coerce_args(schema: dict, value: Any, path: str = "$") -> tuple[Any, list[str]]:
    """Deterministic, logged repair of one provider quirk: an array/object argument sent as a JSON string (seen with
    Qwen on OpenAI-compatible tool calls). Only a string that parses to exactly the expected container type is
    replaced; everything else is left for strict validation to reject."""
    import json

    types = schema.get("type")
    tlist = types if isinstance(types, list) else [types]
    if isinstance(value, str) and ("array" in tlist or "object" in tlist) and "string" not in tlist:
        try:
            parsed = json.loads(value)
        except ValueError:
            return value, []
        if ("array" in tlist and isinstance(parsed, list)) or ("object" in tlist and isinstance(parsed, dict)):
            inner, paths = coerce_args(schema, parsed, path)
            return inner, [path, *paths]
        return value, []
    paths: list[str] = []
    if isinstance(value, dict):
        props = schema.get("properties", {})
        out = dict(value)
        for key, sub in props.items():
            if key in out:
                out[key], p = coerce_args(sub, out[key], f"{path}.{key}")
                paths += p
        return out, paths
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        out_l = []
        for i, v in enumerate(value):
            nv, p = coerce_args(schema["items"], v, f"{path}[{i}]")
            out_l.append(nv)
            paths += p
        return out_l, paths
    return value, paths


def validate_args(schema: dict, value: Any, path: str = "$") -> None:
    """Minimal JSON-schema validator for the subset our tool schemas use (strict mode semantics)."""
    types = schema.get("type")
    if types is not None:
        tlist = types if isinstance(types, list) else [types]
        ok = False
        for t in tlist:
            py = _TYPES[t]
            if t == "integer" and isinstance(value, bool):
                continue
            if t == "number" and isinstance(value, bool):
                continue
            if isinstance(value, py):
                ok = True
                break
        if not ok:
            raise ArgError(f"{path}: expected {types}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise ArgError(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ArgError(f"{path}: longer than {schema['maxLength']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ArgError(f"{path}: shorter than {schema['minLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ArgError(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ArgError(f"{path}: above {schema['maximum']}")
    if isinstance(value, dict) and schema.get("type") in ("object", ["object", "null"]) or isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ArgError(f"{path}: missing required field {key!r}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise ArgError(f"{path}: unexpected fields {sorted(extra)}")
        for key, sub in props.items():
            if key in value:
                validate_args(sub, value[key], f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ArgError(f"{path}: more than {schema['maxItems']} items")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ArgError(f"{path}: fewer than {schema['minItems']} items")
        for i, item in enumerate(value):
            validate_args(schema["items"], item, f"{path}[{i}]")


EXPLORATION = ("list_tables", "describe_table", "inspect_relationships", "sample_rows")
A1_DATA_TOOLS = EXPLORATION + ("validate_sql", "execute_readonly_sql", "get_query_result_metadata")
A2_DATA_TOOLS = A1_DATA_TOOLS + ("compare_query_results",)
