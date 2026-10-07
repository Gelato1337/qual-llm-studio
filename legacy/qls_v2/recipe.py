"""Ontology (core types, the same for every method) and recipes (method-specific types,
link rules and steps, loaded from YAML).

A recipe has two halves:
  * machine-readable rules the server enforces at write time (types, required fields,
    allowed links with minimum counts, required reasons, exclusivity, quote rules,
    memo kinds and memo levels);
  * prose steps for the agent (what each step produces, where the human checks in).

Gioia is one recipe; thematic analysis or a "parallels" recipe are others over the
same core and the same store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .util import QlsError, dumps, sha256_text

ANY = "*"


@dataclass
class LinkSpec:
    rel: str
    to: list[str]                 # target types; "Source" and "*" allowed
    min: int = 0
    max: int | None = None
    reason: str = "optional"      # "required" | "optional"
    exclusive: bool = False       # a target may have at most one active such link from objects of this type
    description: str = ""


@dataclass
class TypeSpec:
    name: str
    prefix: str
    fields: dict[str, dict] = field(default_factory=dict)
    links: dict[str, LinkSpec] = field(default_factory=dict)
    writable_by: str = "agent"    # "agent" | "human" | "server"
    description: str = ""


# -- core ontology -----------------------------------------------------------

def core_types() -> dict[str, TypeSpec]:
    return {
        "Quote": TypeSpec("Quote", "Q", writable_by="server",
                          description="A pointer into a source (source id, version, character offsets). Created only by add_quote, which verifies it."),
        "Memo": TypeSpec("Memo", "M", fields={"kind": {"required": True}, "text": {"required": True}, "level": {}},
                         links={"about": LinkSpec("about", [ANY], min=1, description="what the memo is about"),
                                "cites": LinkSpec("cites", ["Memo", "Quote", ANY], description="evidence or lower-level memos it builds on")},
                         description="Content written down so later steps can use it. Always about at least one object."),
        "CodebookEntry": TypeSpec("CodebookEntry", "CB",
                                  fields={"code": {"required": True}, "brief": {"required": True}, "full": {},
                                          "when_to_use": {"required": True}, "when_not_to_use": {}, "version": {}},
                                  links={"example": LinkSpec("example", ["Quote"]), "uses": LinkSpec("uses", ["Term"])},
                                  writable_by="human",
                                  description="Shared rule for a code (MacQueen et al. 1998 format). Changed by humans/reconciler only."),
        "Term": TypeSpec("Term", "TR", fields={"term": {"required": True}, "definition": {}},
                         writable_by="human", description="Accepted canonical term. Agents propose; they never add."),
        "Proposal": TypeSpec("Proposal", "P", fields={"kind": {"required": True}, "text": {"required": True}, "term": {}},
                             links={"about": LinkSpec("about", [ANY])},
                             description="A proposed term or codebook change, merged in reconciliation rounds."),
        "Residual": TypeSpec("Residual", "R", fields={"text": {"required": True}},
                             links={"from": LinkSpec("from", ["Source"], min=1, max=1),
                                    "evidenced_by": LinkSpec("evidenced_by", ["Quote"])},
                             description="Something in a source that does not fit the current codebook."),
        "BoardPost": TypeSpec("BoardPost", "B", fields={"text": {"required": True}},
                              links={"about": LinkSpec("about", [ANY])}, description="Shared board message between agents."),
    }


MEMO_LEVELS = {"interview": {"cites": None}, "batch": {"cites": "interview"}, "corpus": {"cites": "batch"}}


@dataclass
class Recipe:
    name: str
    version: str
    description: str
    types: dict[str, TypeSpec]
    memo_kinds: list[str]
    memo_budget: dict[str, int]
    quote: dict[str, Any]
    steps: list[dict]
    raw: dict
    hash: str

    def type(self, name: str) -> TypeSpec:
        if name not in self.types:
            raise QlsError(f"Unknown type {name!r}. Types in recipe {self.name!r}: {', '.join(self.types)}")
        return self.types[name]

    def prefix_type(self, oid: str) -> str | None:
        prefix = oid.split("-", 1)[0]
        return next((t.name for t in self.types.values() if t.prefix == prefix), None)

    def summary(self) -> dict:
        return {
            "name": self.name, "version": self.version, "hash": self.hash, "description": self.description,
            "types": {t.name: {"prefix": t.prefix, "writable_by": t.writable_by, "description": t.description,
                               "fields": t.fields,
                               "links": {r: {"to": l.to, "min": l.min, "max": l.max, "reason": l.reason,
                                             "exclusive": l.exclusive, "description": l.description}
                                         for r, l in t.links.items()}}
                      for t in self.types.values()},
            "memo_kinds": self.memo_kinds, "memo_levels": list(MEMO_LEVELS), "memo_budget": self.memo_budget,
            "quote": self.quote, "steps": self.steps,
        }


def load_recipe(name_or_path: str) -> Recipe:
    import yaml

    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.exists():
        text = p.read_text(encoding="utf-8")
    else:
        try:
            text = resources.files("qls.recipes").joinpath(f"{name_or_path}.yaml").read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise QlsError(f"No recipe {name_or_path!r} (built-in: {', '.join(builtin_recipes())})") from exc
    raw = yaml.safe_load(text)
    types = core_types()
    for name, spec in (raw.get("types") or {}).items():
        if name in types:
            raise QlsError(f"Recipe redefines core type {name}")
        links = {}
        for rel, ls in (spec.get("links") or {}).items():
            to = ls["to"] if isinstance(ls["to"], list) else [ls["to"]]
            links[rel] = LinkSpec(rel, to, int(ls.get("min", 0)), ls.get("max"), ls.get("reason", "optional"),
                                  bool(ls.get("exclusive", False)), ls.get("description", ""))
        fields = {f: (v or {}) for f, v in (spec.get("fields") or {}).items()}
        types[name] = TypeSpec(name, spec["prefix"], fields, links, spec.get("writable_by", "agent"), spec.get("description", ""))
    prefixes = [t.prefix for t in types.values()]
    if len(prefixes) != len(set(prefixes)):
        raise QlsError("Recipe type prefixes must be unique")
    return Recipe(
        name=raw["name"], version=str(raw.get("version", "1")), description=raw.get("description", ""), types=types,
        memo_kinds=list(raw.get("memo_kinds") or ["meaning", "informant", "surprise", "uncertainty", "method"]),
        memo_budget={**{"interview": 4000, "batch": 6000, "corpus": 10000}, **(raw.get("memo_budget") or {})},
        quote={"threshold": 90, "roles": ["informant"], **(raw.get("quote") or {})},
        steps=list(raw.get("steps") or []), raw=raw, hash=sha256_text(dumps(raw)),
    )


def builtin_recipes() -> list[str]:
    return sorted(p.name[:-5] for p in resources.files("qls.recipes").iterdir() if p.name.endswith(".yaml"))
