"""Extension fields: node keys beyond id/gist/notes/touches, checked at the write boundary.

The product owns one field with behaviour, `locked`: a rule the person wrote,
which agents cannot rewrite, delete or rename and compaction never evicts
(store.put_node, core.compactor). It guards against accidents, not a hostile
agent: the admin surfaces (REST, the visual editor) are on localhost.

Every other field is DECLARED by the storage root's fields.json, so a module
running its own instance (on its own storage root) brings its vocabulary
without the product carrying it. A root without the file has no extension
fields, and its agents see no new tool parameters:

  {
    "tags":      {"agent": true},
    "kind":      {"type": "enum", "values": ["fact", "decision"], "agent": true},
    "util":      {"type": "number"},
    "review_at": {"type": "number", "min": 0, "agent": true}
  }

Types: enum (values), number (min/max), bool, string (max length), tags.
"agent": true lets MCP write the field; otherwise only the admin surfaces can.
Fields are stored as top-level node keys (the shape existing module graphs
have on disk). A value of None removes the field.
"""

import json
import logging
import re
from pathlib import Path

from .exceptions import KGError

logger = logging.getLogger(__name__)

FIELDS_FILE = "fields.json"
LOCKED_FIELD = "locked"
TAGS_FIELD = "tags"

_RESERVED = {"id", "gist", "notes", "touches", "level", "session_id", LOCKED_FIELD}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# Bare kind ("general") or kind:name ("area:marketing", "person:alex").
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}(:[A-Za-z0-9][A-Za-z0-9._/-]{0,127})?$")
_TYPES = ("enum", "number", "bool", "string", "tags")
MAX_TAGS = 32
STRING_MAX_DEFAULT = 500


def is_locked(node: dict) -> bool:
    return node.get(LOCKED_FIELD) is True


def _check_spec(name: str, spec) -> dict:
    if not _NAME_RE.match(name) or name in _RESERVED:
        raise ValueError(f"field name {name!r} is reserved or not lowercase snake_case")
    if not isinstance(spec, dict):
        raise ValueError(f"field {name!r}: spec must be an object")
    spec = dict(spec)
    spec.setdefault("type", "tags" if name == TAGS_FIELD else None)
    if spec["type"] not in _TYPES:
        raise ValueError(f"field {name!r}: type must be one of {', '.join(_TYPES)}")
    if spec["type"] == "enum":
        values = spec.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            raise ValueError(f"field {name!r}: enum needs a non-empty list of string values")
    if not isinstance(spec.get("agent", False), bool):
        raise ValueError(f"field {name!r}: agent must be true or false")
    return spec


class FieldSchema:
    """The extension fields one storage root declares."""

    def __init__(self, specs: dict | None = None, error: str | None = None):
        self.specs = specs or {}
        self.error = error

    @property
    def agent_keys(self) -> set[str]:
        return {name for name, spec in self.specs.items() if spec.get("agent")}

    @property
    def tags_enabled(self) -> bool:
        return self.specs.get(TAGS_FIELD, {}).get("type") == "tags"

    def validate(self, fields: dict, admin: bool) -> dict:
        """Normalized {name: value}; None means remove. Raises KGError."""
        if not isinstance(fields, dict):
            raise KGError("fields must be an object")
        out = {}
        for name, value in fields.items():
            if name == LOCKED_FIELD:
                if not admin:
                    raise KGError("'locked' is set by a person (REST or the visual editor), not by an agent")
                if value is not None and not isinstance(value, bool):
                    raise KGError("locked must be true or false")
                out[name] = True if value else None
                continue
            spec = self.specs.get(name)
            if spec is None:
                declared = ", ".join(sorted(self.specs)) or "none"
                hint = f" (fields.json not loaded: {self.error})" if self.error else ""
                raise KGError(f"Unknown node field {name!r}; this memory declares: {declared}{hint}")
            if not admin and not spec.get("agent"):
                raise KGError(f"Field {name!r} is written by an admin surface, not by an agent")
            out[name] = None if value is None else _check_value(name, spec, value)
        return out

    def describe(self) -> str:
        if self.error:
            return f"{FIELDS_FILE} not loaded: {self.error}"
        if not self.specs:
            return "no extension fields"
        return "fields: " + ", ".join(
            f"{n} ({s['type']}{', agent' if s.get('agent') else ''})" for n, s in sorted(self.specs.items()))


def _check_value(name: str, spec: dict, value):
    kind = spec["type"]
    if kind == "tags":
        if (not isinstance(value, list) or len(value) > MAX_TAGS
                or not all(isinstance(t, str) for t in value)):
            raise KGError(f"{name} must be a list of at most {MAX_TAGS} strings")
        for tag in value:
            if not _TAG_RE.match(tag):
                raise KGError(f"Invalid tag {tag!r}: lowercase kind, optional ':name' "
                              f"(e.g. 'general', 'area:marketing')")
        return list(dict.fromkeys(value))
    if kind == "enum":
        if value not in spec["values"]:
            raise KGError(f"{name} must be one of {', '.join(spec['values'])}, got {value!r}")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise KGError(f"{name} must be true or false")
        return value
    if kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise KGError(f"{name} must be a number")
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise KGError(f"{name} must be within [{lo}, {hi}]")
        return value
    limit = spec.get("max", STRING_MAX_DEFAULT)
    if not isinstance(value, str) or len(value) > limit:
        raise KGError(f"{name} must be a string of at most {limit} characters")
    return value


_cache: dict[str, tuple[int, FieldSchema]] = {}


def load_schema(root: Path) -> FieldSchema:
    """The root's schema, re-read when fields.json changes. A broken file is
    logged and treated as declaring nothing, so memory keeps working and every
    write that needs a field is refused with the reason."""
    path = Path(root) / FIELDS_FILE
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return FieldSchema()
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
        schema = FieldSchema({name: _check_spec(name, spec) for name, spec in data.items()})
    except (OSError, ValueError) as e:
        logger.error(f"{path}: {e}; running without extension fields")
        schema = FieldSchema(error=str(e))
    _cache[str(path)] = (mtime, schema)
    return schema


def tool_params(schema: FieldSchema) -> tuple[dict, dict]:
    """Extra JSON-schema properties for kg_put_node and kg_search: only what
    this root lets agents write, so a root without fields.json adds nothing."""
    put, search = {}, {}
    if schema.tags_enabled and schema.specs[TAGS_FIELD].get("agent"):
        tags = {"type": "array", "items": {"type": "string"},
                "description": "Scope tags, 'kind' or 'kind:name' (e.g. 'general', 'area:marketing'). "
                               "Replaces the stored list"}
        put[TAGS_FIELD] = tags
        search[TAGS_FIELD] = {"type": "array", "items": {"type": "string"},
                              "description": "Only nodes carrying any of these tags"}
    props = {}
    for name in sorted(schema.agent_keys - {TAGS_FIELD}):
        spec = schema.specs[name]
        kind = spec["type"]
        if kind == "enum":
            prop = {"type": "string", "enum": spec["values"]}
        elif kind == "number":
            prop = {"type": "number"}
            if spec.get("min") is not None:
                prop["minimum"] = spec["min"]
            if spec.get("max") is not None:
                prop["maximum"] = spec["max"]
        elif kind == "bool":
            prop = {"type": "boolean"}
        elif kind == "tags":
            prop = {"type": "array", "items": {"type": "string"}}
        else:
            prop = {"type": "string", "maxLength": spec.get("max", STRING_MAX_DEFAULT)}
        props[name] = {"anyOf": [prop, {"type": "null"}]}
    if props:
        put["fields"] = {"type": "object", "properties": props, "additionalProperties": False,
                         "description": "Extension fields this memory declares; null removes one"}
    return put, search
