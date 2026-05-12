"""
Recipe schema.

A recipe is a JSON file describing one LLM-driven transformation of a
dataset. All recipes use type="custom_extract" — they carry a free-form
prompt template (with {{column}} placeholders), an output schema, and
optional post-processing.

Lightweight, demo-friendly, runs fine on small local models. Each row
is processed in parallel up to a configurable n_parallel.

Recipes are validated on load. Bad recipes fail fast with a clear error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict


def _parse_listish(raw: str) -> list | None:
    """Parse a list rendered with either JSON or Python repr.

    Handles both `["a", "b"]` and `['a', 'b']`. Returns None if not parseable.
    """
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        try:
            import ast
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return None
    return parsed if isinstance(parsed, list) else None

from pathlib import Path
from typing import Any, Literal




# ---------------------------------------------------------------------------
# Output schema (custom_extract only)
# ---------------------------------------------------------------------------


@dataclass
class FieldSpec:
    """One field in a custom_extract output row.

    type=text   : any string
    type=number : numeric, optionally bounded; integer=True for ints only
    type=enum   : value must be in `allowed`
    type=list   : a list of items. By default items are strings; if `item_type`
                  is set to "enum" then each item must be in `allowed`.
                  `min` / `max` here bound list length, not values.
    """

    name: str
    type: Literal["text", "number", "enum", "list"] = "text"
    description: str = ""
    required: bool = True
    # number constraints (or list-length constraints when type=list)
    min: float | None = None
    max: float | None = None
    integer: bool = False
    # enum constraint
    allowed: list[str] | None = None
    # list constraint
    item_type: Literal["text", "enum"] = "text"

    def validate_value(self, value: Any) -> tuple[bool, str | None]:
        """Return (ok, error_message). Used by the validation loop."""
        # Empty / missing handling. For list-type fields, an empty list is
        # an acceptable "missing" representation only if not required.
        if value is None or (isinstance(value, str) and not value.strip()):
            if self.required:
                return False, f"field '{self.name}' is required but missing or empty"
            return True, None

        if self.type == "text":
            if not isinstance(value, (str, int, float)):
                return False, f"field '{self.name}' must be text, got {type(value).__name__}"
            return True, None

        if self.type == "number":
            try:
                num = float(value)
            except (TypeError, ValueError):
                return False, f"field '{self.name}' must be a number, got {value!r}"
            if self.integer and num != int(num):
                return False, f"field '{self.name}' must be an integer, got {num}"
            if self.min is not None and num < self.min:
                return False, f"field '{self.name}' = {num} is below minimum {self.min}"
            if self.max is not None and num > self.max:
                return False, f"field '{self.name}' = {num} is above maximum {self.max}"
            return True, None

        if self.type == "enum":
            if not self.allowed:
                return False, f"field '{self.name}' is enum but no `allowed` values set"
            if str(value) not in self.allowed:
                return (
                    False,
                    f"field '{self.name}' = {value!r} not in allowed: {self.allowed}",
                )
            return True, None

        if self.type == "list":
            if not isinstance(value, list):
                return False, f"field '{self.name}' must be a list, got {type(value).__name__}"
            if self.required and len(value) == 0:
                return False, f"field '{self.name}' is required but the list is empty"
            if self.min is not None and len(value) < self.min:
                return False, f"field '{self.name}' has {len(value)} items, below minimum {int(self.min)}"
            if self.max is not None and len(value) > self.max:
                return False, f"field '{self.name}' has {len(value)} items, above maximum {int(self.max)}"
            if self.item_type == "enum":
                if not self.allowed:
                    return False, f"field '{self.name}' has item_type=enum but no `allowed` values set"
                bad = [v for v in value if str(v) not in self.allowed]
                if bad:
                    return False, f"field '{self.name}' has items {bad!r} not in allowed: {self.allowed}"
            return True, None

        return False, f"field '{self.name}' has unknown type {self.type!r}"


@dataclass
class OutputSpec:
    """Shape of one LLM call's output.

    shape=list   : the model returns a list of objects under `list_key`.
                   Each item becomes a row.
    shape=single : the model returns one object that becomes one row.

    count: bounds on the number of items (list shape only). None = open.
           Use {"min": 1, "max": 5} or {"exact": 3}.
    """

    fields: list[FieldSpec]
    shape: Literal["list", "single"] = "list"
    list_key: str = "items"
    count: dict[str, int] | None = None


# ---------------------------------------------------------------------------
# Inputs (both families)
# ---------------------------------------------------------------------------


@dataclass
class Inputs:
    """Where data comes from.

    target  : column name in the input df — the main subject of each call
              (used by legacy {target} placeholder; new recipes can use
              {{column_name}} directly)
    compare : optional second column; for paired tasks (templates {compare}
              or {{column_name}} into the prompt)
    extra   : constants string-formatted into the prompt as {extra.X}
    """

    target: str
    compare: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# On-invalid behavior (custom_extract retry loop)
# ---------------------------------------------------------------------------


@dataclass
class OnInvalid:
    """How to handle a row whose output fails validation."""

    max_retries: int = 5
    fallback: Literal["drop", "keep_with_warning"] = "drop"


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


@dataclass
class PostProcessStep:
    """A non-LLM transform applied after the LLM call.

    Currently supported types: 'ground_quotes' (fuzzy-match a quote field
    against a source column).
    """

    type: str
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The Recipe itself
# ---------------------------------------------------------------------------


@dataclass
class Recipe:
    name: str
    type: str  # always "custom_extract" now
    inputs: Inputs

    # Free-text description shown in the UI. Helps researchers pick recipes.
    description: str = ""

    # Recipe body
    prompt: str = ""
    output: OutputSpec | None = None
    on_invalid: OnInvalid = field(default_factory=OnInvalid)
    post_process: list[PostProcessStep] = field(default_factory=list)

    # Eval block — read by the Eval tab. All fields optional; if absent,
    # the tab uses sensible defaults (random sampling, generic labels).
    eval_config: dict[str, Any] = field(default_factory=dict)

    # Display / metadata
    tier: int = 1  # 1 = lightweight, 2 = heavy, 3 = utility
    tags: list[str] = field(default_factory=list)

    # ---- Lifecycle ---------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of error strings. Empty list = valid."""
        errors: list[str] = []

        if not self.name:
            errors.append("recipe.name is required")
        if not self.type:
            errors.append("recipe.type is required")
        if not self.inputs.target:
            errors.append("recipe.inputs.target is required")

        if self.type == "custom_extract":
            if not self.prompt:
                errors.append("custom_extract recipes require a `prompt`")
            if self.output is None:
                errors.append("custom_extract recipes require an `output` spec")
            elif not self.output.fields:
                errors.append("output.fields cannot be empty")
            else:
                # Per-field validation: enums need `allowed`; numbers can have min/max
                for f_spec in self.output.fields:
                    if f_spec.type == "enum" and not f_spec.allowed:
                        errors.append(f"field '{f_spec.name}' is enum but has no `allowed` values")
                    if f_spec.type == "list" and f_spec.item_type == "enum" and not f_spec.allowed:
                        errors.append(f"field '{f_spec.name}' has item_type=enum but no `allowed` values")
                    if f_spec.type == "number" and f_spec.min is not None and f_spec.max is not None:
                        if f_spec.min > f_spec.max:
                            errors.append(f"field '{f_spec.name}': min > max")
        else:
            errors.append(f"unknown recipe type: {self.type!r}")

        return errors

    # ---- Rendering for the Chat tab ------------------------------------

    def render_as_text(self) -> str:
        """Produce a single human-readable text view of the recipe.

        The prompt is the centerpiece; output schema, on_invalid, and
        eval config land in small structured blocks at the bottom.

        The format is intentionally easy to round-trip: parse_from_text()
        can reconstruct the recipe from the same string a researcher edits.

        The format is intentionally easy to round-trip: parse_from_text()
        can reconstruct the recipe from the same string a researcher edits.
        """
        out: list[str] = []
        out.append(f"# {self.name}")
        if self.description:
            out.append("")
            out.append(f"> {self.description}")
        out.append("")
        out.append("## PROMPT")
        out.append(self.prompt or "(empty)")
        out.append("")
        if self.output and self.output.fields:
            out.append("## OUTPUT FIELDS")
            out.append(f"shape: {self.output.shape}")
            if self.output.list_key:
                out.append(f"list_key: {self.output.list_key}")
            for f in self.output.fields:
                line = f"- {f.name} ({f.type})"
                if f.required is False:
                    line += " [optional]"
                if f.allowed:
                    line += f"  allowed: {f.allowed}"
                if f.min is not None or f.max is not None:
                    line += f"  range: {f.min}..{f.max}"
                if f.description:
                    line += f"  — {f.description}"
                out.append(line)
            out.append("")
        if self.on_invalid:
            out.append("## ON INVALID")
            out.append(f"max_retries: {self.on_invalid.max_retries}")
            out.append(f"fallback: {self.on_invalid.fallback}")
            out.append("")
        if self.eval_config:
            out.append("## EVAL")
            fields = self.eval_config.get("fields")
            if fields:
                out.append(f"fields: {fields}")
            stratify = self.eval_config.get("stratify_by")
            if stratify:
                out.append(f"stratify_by: {stratify}")
            judge_opts = self.eval_config.get("judge_label_options")
            if judge_opts:
                out.append(f"judge_label_options: {judge_opts}")
            llm_judge = self.eval_config.get("llm_judge", {})
            if llm_judge:
                if "enabled" in llm_judge:
                    out.append(f"llm_judge.enabled: {str(llm_judge['enabled']).lower()}")
                if llm_judge.get("fields"):
                    out.append(f"llm_judge.fields: {llm_judge['fields']}")
                if llm_judge.get("criteria"):
                    out.append(f"llm_judge.criteria: {llm_judge['criteria']}")
                if llm_judge.get("label_options"):
                    out.append(f"llm_judge.label_options: {llm_judge['label_options']}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    @classmethod
    def parse_from_text(cls, text: str, *, base: "Recipe | None" = None) -> "Recipe":
        """Reconstruct a Recipe from its text rendering.

        Strict-ish parser tuned for the format produced by render_as_text.
        Editing happens in well-defined sections separated by `## SECTION`.
        Anything outside known sections is treated as a comment and dropped.

        `base` lets the caller pass in the Recipe before editing so we can
        carry over fields the text format doesn't fully express (e.g.
        post-processing steps). Without a base, we produce a minimal Recipe.
        """
        # Split into name, sections, and section bodies
        name = ""
        description = ""
        sections: dict[str, list[str]] = {}
        cur: str | None = None

        for line in text.splitlines():
            stripped = line.rstrip()
            if stripped.startswith("# "):
                if not name:
                    name = stripped[2:].strip()
                continue
            if stripped.startswith("## "):
                cur = stripped[3:].strip().upper()
                sections[cur] = []
                continue
            if stripped.startswith("> ") and cur is None:
                description += (stripped[2:].rstrip() + "\n")
                continue
            if cur is not None:
                sections[cur].append(line)

        # Now interpret sections
        prompt = "\n".join(sections.get("PROMPT", [])).rstrip() if "PROMPT" in sections else ""
        output_obj: OutputSpec | None = None
        on_invalid = OnInvalid()
        post_process: list[PostProcessStep] = []
        eval_config: dict[str, Any] = {}
        inputs_target = ""
        inputs_compare = None

        # If a base recipe was provided, inherit its non-text-only fields
        if base is not None:
            output_obj = base.output
            on_invalid = base.on_invalid
            post_process = list(base.post_process)
            eval_config = dict(base.eval_config) if base.eval_config else {}
            inputs_target = base.inputs.target
            inputs_compare = base.inputs.compare

        # OUTPUT FIELDS — parse each `- name (type)` line
        if "OUTPUT FIELDS" in sections:
            output_obj = cls._parse_output_section(sections["OUTPUT FIELDS"])

        # INPUTS — used by recipes that explicitly declare target/compare
        if "INPUTS" in sections:
            for line in sections["INPUTS"]:
                line = line.strip()
                m = re.match(r"target column:\s*`?([^`]+)`?", line)
                if m:
                    inputs_target = m.group(1).strip()
                m = re.match(r"compare column:\s*`?([^`]+)`?", line)
                if m:
                    inputs_compare = m.group(1).strip()

        # ON INVALID
        if "ON INVALID" in sections:
            for line in sections["ON INVALID"]:
                line = line.strip()
                if line.startswith("max_retries:"):
                    try:
                        on_invalid.max_retries = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                if line.startswith("fallback:"):
                    on_invalid.fallback = line.split(":", 1)[1].strip()

        # EVAL
        if "EVAL" in sections:
            for line in sections["EVAL"]:
                line = line.strip()
                if line.startswith("fields:"):
                    raw = line.split(":", 1)[1].strip()
                    parsed_list = _parse_listish(raw)
                    if parsed_list is not None:
                        eval_config["fields"] = parsed_list
                if line.startswith("stratify_by:"):
                    eval_config["stratify_by"] = line.split(":", 1)[1].strip()
                if line.startswith("judge_label_options:"):
                    raw = line.split(":", 1)[1].strip()
                    parsed_list = _parse_listish(raw)
                    if parsed_list is not None:
                        eval_config["judge_label_options"] = parsed_list
                if line.startswith("llm_judge.enabled:"):
                    raw = line.split(":", 1)[1].strip().lower()
                    eval_config.setdefault("llm_judge", {})["enabled"] = raw in ("true", "yes", "1")
                if line.startswith("llm_judge.fields:"):
                    raw = line.split(":", 1)[1].strip()
                    parsed_list = _parse_listish(raw)
                    if parsed_list is not None:
                        eval_config.setdefault("llm_judge", {})["fields"] = parsed_list
                if line.startswith("llm_judge.criteria:"):
                    eval_config.setdefault("llm_judge", {})["criteria"] = (
                        line.split(":", 1)[1].strip()
                    )
                if line.startswith("llm_judge.label_options:"):
                    raw = line.split(":", 1)[1].strip()
                    parsed_list = _parse_listish(raw)
                    if parsed_list is not None:
                        eval_config.setdefault("llm_judge", {})["label_options"] = parsed_list

        return cls(
            name=name or (base.name if base else "untitled"),
            type="custom_extract",
            inputs=Inputs(
                target=inputs_target or "text",
                compare=inputs_compare,
                extra=(base.inputs.extra if base else {}),
            ),
            description=(description.strip() or (base.description if base else "")),
            prompt=prompt,
            output=output_obj,
            on_invalid=on_invalid,
            post_process=post_process,
            eval_config=eval_config,
            tier=(base.tier if base else 1),
            tags=(list(base.tags) if base else []),
        )

    @staticmethod
    def _parse_output_section(lines: list[str]) -> OutputSpec | None:
        """Parse the OUTPUT FIELDS section into an OutputSpec."""
        shape = "object"
        list_key = None
        fields: list[FieldSpec] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("shape:"):
                shape = line.split(":", 1)[1].strip()
                continue
            if line.startswith("list_key:"):
                list_key = line.split(":", 1)[1].strip()
                continue
            if line.startswith("- "):
                f = Recipe._parse_field_line(line[2:])
                if f:
                    fields.append(f)
        if not fields:
            return None
        return OutputSpec(shape=shape, list_key=list_key, fields=fields)

    @staticmethod
    def _parse_field_line(text: str) -> FieldSpec | None:
        """Parse one `name (type) [optional] allowed: [...] range: a..b — desc` line."""
        m = re.match(r"^([A-Za-z_][\w\-]*)\s*\(([A-Za-z_]+)\)", text)
        if not m:
            return None
        name = m.group(1)
        ftype = m.group(2)
        rest = text[m.end():]
        f = FieldSpec(name=name, type=ftype)
        if "[optional]" in rest:
            f.required = False

        # allowed: [a, b, c] — accept both Python repr and JSON shapes
        ma = re.search(r"allowed:\s*(\[[^\]]*\])", rest)
        if ma:
            raw = ma.group(1)
            parsed: Any = None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Try Python literal eval as a fallback (handles single quotes)
                try:
                    import ast
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError):
                    parsed = None
            if isinstance(parsed, list):
                f.allowed = parsed

        # range: 1..5
        mr = re.search(r"range:\s*([0-9.\-]+)\.\.([0-9.\-]+)", rest)
        if mr:
            try:
                f.min = float(mr.group(1)) if "." in mr.group(1) else int(mr.group(1))
                f.max = float(mr.group(2)) if "." in mr.group(2) else int(mr.group(2))
            except ValueError:
                pass

        # — description (em-dash separated; only after we've consumed the structured bits)
        if "—" in rest:
            desc = rest.split("—", 1)[1].strip()
            # If `desc` starts with another structured token, it's not a description
            if not desc.startswith(("allowed:", "range:")):
                f.description = desc
        return f

    # ---- Serialization -----------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Recipe":
        """Build a Recipe from a parsed JSON dict, with light defaults filling."""
        # Inputs
        inp = data.get("inputs", {})
        if isinstance(inp, str):
            inp = {"target": inp}
        inputs = Inputs(
            target=inp.get("target", ""),
            compare=inp.get("compare"),
            extra=inp.get("extra", {}) or {},
        )

        # Output (only for custom_extract)
        output_obj: OutputSpec | None = None
        out_raw = data.get("output")
        if out_raw:
            fields = [
                FieldSpec(
                    name=f["name"],
                    type=f.get("type", "text"),
                    description=f.get("description", ""),
                    required=f.get("required", True),
                    min=f.get("min"),
                    max=f.get("max"),
                    integer=f.get("integer", False),
                    allowed=f.get("allowed"),
                    item_type=f.get("item_type", "text"),
                )
                for f in out_raw.get("fields", [])
            ]
            output_obj = OutputSpec(
                fields=fields,
                shape=out_raw.get("shape", "list"),
                list_key=out_raw.get("list_key", "items"),
                count=out_raw.get("count"),
            )

        # OnInvalid
        oi_raw = data.get("on_invalid", {})
        on_invalid = OnInvalid(
            max_retries=oi_raw.get("max_retries", 5),
            fallback=oi_raw.get("fallback", "drop"),
        )

        # Post-process
        pp = [
            PostProcessStep(type=p["type"], params=p.get("params", {}))
            for p in data.get("post_process", [])
        ]

        return cls(
            name=data.get("name", ""),
            type=data.get("type", ""),
            description=data.get("description", ""),
            inputs=inputs,
            prompt=data.get("prompt", ""),
            output=output_obj,
            on_invalid=on_invalid,
            post_process=pp,
            eval_config=data.get("eval", {}) or {},
            tier=data.get("tier", 1),
            tags=data.get("tags", []),
        )

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for JSON dumping. Drops empties."""
        d: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "tier": self.tier,
            "inputs": {
                "target": self.inputs.target,
            },
        }
        if self.inputs.compare:
            d["inputs"]["compare"] = self.inputs.compare
        if self.inputs.extra:
            d["inputs"]["extra"] = self.inputs.extra

        d["prompt"] = self.prompt
        if self.output:
            d["output"] = {
                "shape": self.output.shape,
                "list_key": self.output.list_key,
                "fields": [
                    {k: v for k, v in asdict(f).items() if v not in (None, False, "")}
                    for f in self.output.fields
                ],
            }
            if self.output.count:
                d["output"]["count"] = self.output.count
        d["on_invalid"] = {
            "max_retries": self.on_invalid.max_retries,
            "fallback": self.on_invalid.fallback,
        }
        if self.post_process:
            d["post_process"] = [
                {"type": p.type, "params": p.params} for p in self.post_process
            ]

        if self.eval_config:
            d["eval"] = self.eval_config
        if self.tags:
            d["tags"] = self.tags
        return d


# ---- Loading helpers ---------------------------------------------------------


class RecipeError(ValueError):
    pass


def load_recipe(path: str | Path) -> Recipe:
    """Read a JSON recipe file and validate it. Raises RecipeError on failure."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipeError(f"Recipe {path} is not valid JSON: {exc}") from exc

    recipe = Recipe.from_dict(data)
    errors = recipe.validate()
    if errors:
        details = "\n  - ".join(errors)
        raise RecipeError(f"Recipe {path.name} is invalid:\n  - {details}")
    return recipe


def save_recipe(recipe: Recipe, path: str | Path) -> Path:
    """Write a Recipe to disk as JSON. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe.to_dict(), indent=2, ensure_ascii=False))
    return path


def list_recipes(recipes_root: str | Path) -> list[Recipe]:
    """Load all valid recipes from a directory tree. Skips invalid ones with a warning."""
    recipes_root = Path(recipes_root)
    if not recipes_root.exists():
        return []
    out: list[Recipe] = []
    for p in sorted(recipes_root.rglob("*.json")):
        try:
            out.append(load_recipe(p))
        except RecipeError as exc:
            # Don't crash on a bad recipe file — log and skip
            print(f"[recipe] skipping {p}: {exc}")
    return out
