"""A method is a folder of three files:

    schema.tql     the nouns: types and the rules the database enforces (on top of core.tql)
    actions.yaml   the verbs: which objects agents create, their fields, how groups are formed,
                   which checks to run (soft expectations, as TypeQL)
    method.md      how to think: the open-ended guide the agent reads and interprets

The server generates its method tools from actions.yaml, so a new method needs no server code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from .util import QlsError, sha256_text

CORE_PREFIXES = {"quote": "Q", "memo": "M", "codebook-entry": "CB"}


@dataclass
class Field:
    param: str
    attr: str
    required: bool = False
    doc: str = ""


@dataclass
class Group:
    type: str              # the group type (theme)
    relation: str          # theme-membership
    group_role: str        # theme
    member_role: str       # concept
    member_type: str       # concept
    min: int = 1
    exclusive: bool = True


@dataclass
class Action:
    name: str
    kind: str              # create_code | create_group
    type: str
    doc: str = ""
    members_param: str | None = None
    answers: str = "no"    # no | optional | required


@dataclass
class Method:
    name: str
    title: str
    version: int
    root: Path | None
    schema: str
    guide: str
    raw: dict
    prefixes: dict[str, str]
    fields: dict[str, list[Field]]
    groups: list[Group]
    actions: dict[str, Action]
    checks: list[dict] = field(default_factory=list)
    hash: str = ""

    @property
    def code_type(self) -> str:
        return self.raw["code_type"]

    def levels(self) -> list[str]:
        """Bottom-up: code type, then each group type that groups the level below."""
        out = [self.code_type]
        while True:
            g = next((g for g in self.groups if g.member_type == out[-1]), None)
            if g is None:
                return out
            out.append(g.type)

    def group_of(self, group_type: str) -> Group | None:
        return next((g for g in self.groups if g.type == group_type), None)

    def group_for_member(self, member_type: str) -> Group | None:
        return next((g for g in self.groups if g.member_type == member_type), None)

    def prefix(self, typ: str) -> str:
        return self.prefixes.get(typ) or CORE_PREFIXES[typ]

    def type_of_id(self, oid: str) -> str | None:
        p = oid.rsplit("-", 1)[0]
        return next((t for t, pre in {**CORE_PREFIXES, **self.prefixes}.items() if pre == p), None)

    def memo_budget(self, level: str) -> int:
        return int((self.raw.get("memo") or {}).get("budgets", {}).get(level, 4000))

    def summary(self) -> dict:
        return {
            "method": self.name, "title": self.title, "version": self.version,
            "levels_bottom_up": self.levels(),
            "types": {t: {"fields": {f.param: {"required": f.required, "doc": f.doc} for f in fs}} for t, fs in self.fields.items()},
            "groups": [{"group": g.type, "members": g.member_type, "min": g.min, "exclusive": g.exclusive} for g in self.groups],
            "actions": {a.name: a.doc for a in self.actions.values()},
            "checks": [{"id": c["id"], "text": c["text"]} for c in self.checks],
        }


def core_schema() -> str:
    return resources.files("qls.schema").joinpath("core.tql").read_text(encoding="utf-8")


def builtin_methods() -> list[str]:
    root = resources.files("qls.methods")
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.joinpath("actions.yaml").is_file())


def load_method(name_or_path: str) -> Method:
    p = Path(name_or_path)
    if p.is_dir():
        files = {f: (p / f).read_text(encoding="utf-8") for f in ("schema.tql", "actions.yaml", "method.md")}
        root = p.resolve()
    else:
        d = resources.files("qls.methods").joinpath(name_or_path)
        if not d.joinpath("actions.yaml").is_file():
            raise QlsError(f"Unknown method {name_or_path!r}. Built in: {', '.join(builtin_methods())}; or give a folder.")
        files = {f: d.joinpath(f).read_text(encoding="utf-8") for f in ("schema.tql", "actions.yaml", "method.md")}
        root = None
    raw = yaml.safe_load(files["actions.yaml"])
    fields = {t: [Field(param=k, attr=v.get("attr", k), required=bool(v.get("required")), doc=v.get("doc", ""))
                  for k, v in (spec or {}).items()] for t, spec in (raw.get("fields") or {}).items()}
    groups = [Group(**g) for g in raw.get("groups") or []]
    actions = {k: Action(name=k, kind=v["kind"], type=v["type"], doc=" ".join(str(v.get("doc", "")).split()),
                         members_param=v.get("members_param"), answers=v.get("answers", "no"))
               for k, v in (raw.get("actions") or {}).items()}
    for a in actions.values():
        if a.kind not in ("create_code", "create_group"):
            raise QlsError(f"action {a.name}: unknown kind {a.kind!r}")
        if a.kind == "create_group" and (not a.members_param or not any(g.type == a.type for g in groups)):
            raise QlsError(f"action {a.name}: a group action needs members_param and a groups entry for {a.type}")
    m = Method(name=raw["name"], title=raw.get("title", raw["name"]), version=int(raw.get("version", 1)), root=root,
               schema=files["schema.tql"], guide=files["method.md"], raw=raw, prefixes=raw.get("prefixes") or {},
               fields=fields, groups=groups, actions=actions, checks=raw.get("checks") or [])
    m.hash = sha256_text(core_schema() + "\n" + "\n".join(files[f] for f in ("schema.tql", "actions.yaml", "method.md")))
    return m
