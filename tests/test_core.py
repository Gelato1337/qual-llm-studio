from pathlib import Path

import pytest

from qls.analysis import compare
from qls.project import Project
from qls.tools import Session
from qls.util import QlsError

EX = Path(__file__).resolve().parent.parent / "examples" / "synthetic"


@pytest.fixture
def p(tmp_path):
    p = Project.init(tmp_path / "study")
    for f in sorted(EX.glob("*.txt")):
        p.ingest(f)
    p.store.create_run("r1", "gioia", __import__("qls.recipe").recipe.load_recipe("gioia").hash, {})
    return p


@pytest.fixture
def s(p):
    return Session(p.store, "r1", "agent:test")


def concept(s, label, quotes, reason="shows it"):
    return s.add_object("Concept", {"in_vivo": label, "description": f"says {label}"},
                        [{"rel": "evidenced_by", "to": q, "reason": reason} for q in quotes])


def test_sources_are_offset_slices(p):
    src = p.store.source("P01")
    seg = next(u for u in src["units"] if u["kind"] == "segment" and "two weeks" in src["text"][u["start"]:u["end"]])
    assert src["text"][seg["start"]:seg["end"]].startswith("Clients now expect")
    assert seg["question"].startswith("What changes")
    assert p.ingest(EX / "P01.txt")["version"] == 1  # same text: same version


def test_quote_verification(s):
    r = s.add_quote("P01", "Clients now expect us to release every two weeks instead of once a year.")
    assert r["ok"] and r["method"] == "exact"
    q = s.get(r["id"])
    assert q["text"].startswith("Clients now expect") and q["question"].startswith("What changes")
    again = s.add_quote("P01", "Clients now expect us to release every two weeks instead of once a year.")
    assert again["existing"] and again["id"] == r["id"]
    bad = s.add_quote("P01", "Customers want releases every fortnight these days.")
    assert not bad["ok"] and bad["closest"]["unit"].startswith("P01:s")
    fixed = s.add_quote("P01", start=bad["closest"]["start"], end=bad["closest"]["end"])
    assert fixed["ok"]
    interviewer = s.add_quote("P01", "What changes have you seen in how software is delivered?")
    assert not interviewer["ok"]  # interviewer words are never quotable


def test_concept_rules(s):
    q = s.add_quote("P01", "Legacy systems.")["id"]
    assert not s.add_object("Concept", {"in_vivo": "legacy"}, [{"rel": "evidenced_by", "to": q, "reason": "x"}])["ok"]
    assert not s.add_object("Concept", {"in_vivo": "legacy", "description": "d"}, [])["ok"]  # no quote
    assert not s.add_object("Concept", {"in_vivo": "legacy", "description": "d"},
                            [{"rel": "evidenced_by", "to": q}])["ok"]  # no reason
    assert not s.add_object("Concept", {"in_vivo": "l", "description": "d", "mood": "x"},
                            [{"rel": "evidenced_by", "to": q, "reason": "r"}])["ok"]  # unknown field
    r = concept(s, "legacy systems", [q])
    assert r["ok"] and r["id"] == "C-1"
    assert not s.add_object("Quote", {}, [])["ok"]
    assert not s.add_object("Term", {"term": "x"})["ok"]  # agents only propose
    assert s.propose_term("LEGACY", "old systems kept alive", [r["id"]])["ok"]


def test_themes_exclusive_and_minimum(s):
    q1 = s.add_quote("P01", "Legacy systems.")["id"]
    q2 = s.add_quote("P02", "Finding senior people.")["id"]
    q3 = s.add_quote("P02", "We deploy several times a day now.")["id"]
    c1, c2, c3 = (concept(s, l, [q])["id"] for l, q in (("legacy", q1), ("seniors", q2), ("deploy", q3)))
    assert not s.add_object("Theme", {"label": "x", "definition": "d"},
                            [{"rel": "groups", "to": c1, "reason": "r"}])["ok"]  # needs 2 concepts
    t1 = s.add_object("Theme", {"label": "Old and scarce", "definition": "d"},
                      [{"rel": "groups", "to": c1, "reason": "r"}, {"rel": "groups", "to": c2, "reason": "r"}])["id"]
    refused = s.add_object("Theme", {"label": "Other", "definition": "d"},
                           [{"rel": "groups", "to": c1, "reason": "r"}, {"rel": "groups", "to": c3, "reason": "r"}])
    assert not refused["ok"] and "already in" in refused["error"]
    assert not s.unlink(t1, "groups", c1, "move")["ok"]  # would leave one concept
    assert s.link(t1, "groups", c3, "same pressure")["ok"]
    assert s.unlink(t1, "groups", c1, "does not fit")["ok"]


def test_merge_moves_links_and_refuses_conflicts(s):
    qs = [s.add_quote("P01", t)["id"] for t in ("Legacy systems.", "Clients now expect us to release every two weeks instead of once a year.")]
    qs += [s.add_quote("P03", "Vanhat järjestelmät ovat iso ongelma.")["id"]]
    c1, c2, c3 = (concept(s, f"c{i}", [q])["id"] for i, q in enumerate(qs))
    t = s.add_object("Theme", {"label": "t", "definition": "d"},
                     [{"rel": "groups", "to": c1, "reason": "r"}, {"rel": "groups", "to": c2, "reason": "r"}])["id"]
    new = s.add_object("Concept", {"in_vivo": "legacy systems nobody replaces", "description": "d"},
                       [{"rel": "evidenced_by", "to": qs[0], "reason": "r"}, {"rel": "evidenced_by", "to": qs[2], "reason": "r"}])["id"]
    assert s.supersede([c1, c3], new, "same point in two interviews")["ok"]
    assert {l["to"] for l in s.get(t)["links_out"]} == {new, c2}
    assert s.get(c1)["status"] == "superseded"
    assert not s.supersede([c2], None, "")["ok"]  # reason required


def test_memos_levels_and_budget(s):
    assert not s.add_memo([], "meaning", "x")["ok"]
    assert not s.add_memo(["P01"], "feeling", "x")["ok"]
    assert s.add_memo(["P01"], "informant", "Team lead at a consultancy")["ok"]
    m1 = s.update_memo("interview", "P01", "Interview memo v1")
    m2 = s.update_memo("interview", "P01", "Interview memo v2")
    assert s.get_memo("interview", "P01")["memo"]["text"] == "Interview memo v2"
    assert s.get(m1["id"])["status"] == "superseded"
    assert not s.update_memo("batch", "P01", "batch without citations")["ok"]
    assert s.update_memo("batch", "P01", "batch", cites=[m2["id"]])["ok"]
    assert not s.update_memo("interview", "P02", "x" * 10000)["ok"]  # over budget


def test_checkpoint_blocks_until_human_resumes(p, s):
    s.checkpoint("coded P01", ["Is 'legacy' a trend?"])
    assert not s.add_quote("P01", "Legacy systems.")["ok"]
    with pytest.raises(QlsError):
        s.resume()  # agents cannot resume
    h = Session(p.store, "r1", "human:anna")
    h.answer("Yes, if they describe change over time.")
    h.resume()
    assert s.add_quote("P01", "Legacy systems.")["ok"]
    assert s.get_feedback()[0]["text"].startswith("Yes")


def test_fork_replay_and_compare(p, s):
    q1 = s.add_quote("P01", "Legacy systems.")["id"]
    q2 = s.add_quote("P03", "Vanhat järjestelmät ovat iso ongelma.")["id"]
    q3 = s.add_quote("P02", "Finding senior people.")["id"]
    c1, c2, c3 = (concept(s, l, [q])["id"] for l, q in (("a", q1), ("b", q2), ("c", q3)))
    at = s.add_object("Theme", {"label": "t", "definition": "d"},
                      [{"rel": "groups", "to": c1, "reason": "r"}, {"rel": "groups", "to": c2, "reason": "r"}])["event"]
    st = p.store
    st.create_run("alt", "gioia", st.run("r1")["recipe_hash"], {}, parent="r1", fork_at=at - 1)
    alt = Session(st, "alt", "agent:test")
    assert alt.status()["objects"].get("Theme") is None and alt.status()["objects"]["Concept"] == 3
    alt.add_object("Theme", {"label": "t2", "definition": "d"},
                   [{"rel": "groups", "to": c1, "reason": "r"}, {"rel": "groups", "to": c3, "reason": "r"}])
    s.add_quote("P02", "We deploy several times a day now.")  # later event in r1 is not inherited
    assert "Q-4" not in {o["id"] for o in st.objects("alt", "Quote")}
    before = st.state_hash("alt")
    st.rebuild("alt")
    assert st.state_hash("alt") == before
    r = compare(st, "r1", "alt")
    assert r["levels"]["concept"]["ari"] == 1.0 and r["levels"]["theme"]["n"] == 1  # only P01:s005 themed in both


def test_recipe_change_is_detected(p):
    st = p.store
    st.create_run("old", "gioia", "sha256:not-the-current-recipe", {})
    with pytest.raises(QlsError):
        Session(st, "old", "agent:x")


def test_mcp_server_tools(p):
    pytest.importorskip("mcp")
    import asyncio

    from qls.server import build_server

    s = Session(p.store, "r1", "agent:x")
    names = {t.name for t in asyncio.run(build_server(s).list_tools())}
    assert {"add_quote", "add_object", "checkpoint", "ways_of_working", "update_memo"} <= names
    ro = {t.name for t in asyncio.run(build_server(s, readonly=True).list_tools())}
    assert "add_quote" not in ro and "search" in ro
    from qls.server import main

    with pytest.raises(QlsError):
        main(str(p.root), "r1", "human:anna")
