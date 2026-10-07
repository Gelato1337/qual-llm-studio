import json

import pytest

from qls.analyst import process_metrics, run_pi
from qls.coding import code_corpus, consolidate
from qls.compare import compare_runs, consensus, partition_scores
from qls.llm import LLM, LLMResult, MockLLM
from qls.ops import Ops
from qls.project import QlsError
from qls.report import compare_report, run_report
from qls.runs import fork
from qls.views import check, status


def test_stage1_and_stage2_with_mock(project):
    run = code_corpus(project, "code1", log=lambda *_: None)
    m = run.manifest()
    assert m["status"] == "done" and m["grounding"]["quotes_failed"] == 0
    s = run.state()
    assert len(s["concepts"]) == 15
    q = next(iter(s["quotes"].values()))
    seg = project.segment(q["segment"])
    assert seg["text"][q["start"]:q["end"]] == q["text"]  # spans point into the corpus
    assert all(d["actor"] == "pipeline" and d["stage"] == 1 for d in run.decisions())
    cons = consolidate(project, "code1", into="cons1", log=lambda *_: None)
    assert cons.manifest()["parent"] == "code1"
    assert check(cons) == []


class FlakyQuoteLLM(LLM):
    """First answer has a paraphrased quote; the retry fixes it."""

    def __init__(self):
        super().__init__({"provider": "test", "model": "flaky"})
        self.calls = 0

    def json(self, system, user, schema, name):
        self.calls += 1
        seg = "P01:s002"
        quote = "Clients want releases every fortnight" if self.calls == 1 else "Clients now expect us to release every two weeks"
        return LLMResult({"concepts": [{"label": "Clients expect fortnightly releases", "description": "d",
                                        "quotes": [{"segment_id": seg, "quote": quote}]}]}, "", {"model_served": "flaky"})


def test_ungrounded_quote_is_retried_with_feedback(project):
    llm = FlakyQuoteLLM()
    run = code_corpus(project, "flaky", docs=["P01"], llm=llm, log=lambda *_: None)
    g = run.manifest()["grounding"]
    assert llm.calls == 2 and g["recovered_on_retry"] == 1 and g["quotes_failed"] == 0


def test_ops_are_lossless_and_logged(project):
    code_corpus(project, "code1", log=lambda *_: None)
    r = fork(project, "code1", "g1", keep="concepts")
    ops = Ops(r, actor="agent:test")
    t = ops.create_theme("Delivery", "def", ["c2", "c7"], "same point")
    new = ops.merge_concepts(["c2", "c7"], "Faster releases", "d", "same claim")
    assert new in r.state()["themes"][t]["concepts"]  # merge keeps theme membership
    s = r.state()
    parts = ops.split_concept(new, [{"label": "a", "quotes": [s["concepts"][new]["quotes"][0]]},
                                    {"label": "b", "quotes": s["concepts"][new]["quotes"][1:]}], "two points")
    assert len(parts) == 2
    with pytest.raises(QlsError):
        ops.split_concept(parts[0], [{"label": "x", "quotes": []}, {"label": "y", "quotes": []}], "bad")
    with pytest.raises(QlsError):
        ops.create_theme("No reason", "", ["c3"], "")
    ops.create_theme("Other", "", ["c3", parts[0]], "moved")  # moves parts[0] out of t
    assert check(r) == []
    ops.drop_concept("c4", "not a trend")
    assert check(r) == []
    ops.add_memo("note", ["c3"])
    decs = r.decisions()
    assert [d["id"] for d in decs] == [f"dr{i}" for i in range(1, len(decs) + 1)]
    assert {d["actor"] for d in decs[1:]} == {"agent:test"}
    st = status(project, r)
    assert "c4" not in st["unassigned_concepts"]


def test_partition_scores():
    a = {"1": "x", "2": "x", "3": "y", "4": "y"}
    assert partition_scores(a, a) == {"n": 4, "rand": 1.0, "ari": 1.0, "nmi": 1.0}
    b = {"1": "p", "2": "q", "3": "p", "4": "q"}
    sc = partition_scores(a, b)
    assert sc["rand"] == pytest.approx(2 / 6, abs=1e-4) and sc["ari"] < 0


def test_compare_consensus_and_reports(project):
    code_corpus(project, "code1", log=lambda *_: None)
    for name, groups in (("a", [["c2", "c7", "c12"], ["c3", "c8", "c13"]]),
                         ("b", [["c2", "c7", "c12"], ["c3", "c8"], ["c13"]]),
                         ("c", [["c2", "c7", "c12"], ["c3", "c8", "c13"]])):
        r = fork(project, "code1", name, keep="concepts")
        ops = Ops(r, actor="agent:test")
        for i, g in enumerate(groups):
            ops.create_theme(f"T{i}", "", g, "r")
    ac = compare_runs(project, "a", "c")
    assert ac["elements"] == "concept" and ac["levels"]["theme"]["ari"] == 1.0
    ab = compare_runs(project, "a", "b")
    assert ab["levels"]["theme"]["ari"] < 1.0
    cons = consensus(project, ["a", "b", "c"])
    assert cons["concept_stability"]["c13"] < cons["concept_stability"]["c2"]
    html = compare_report(project, "a", "b")
    assert "Only in b" in html and "<table" in html
    assert "Data structure" in run_report(project, "a")


def test_quote_level_compare_for_independent_codings(project):
    code_corpus(project, "x1", log=lambda *_: None)
    code_corpus(project, "x2", log=lambda *_: None)
    Ops(project.run("x1"), "h").merge_concepts(["c1", "c2"], "m", "", "r")
    r = compare_runs(project, "x1", "x2")
    assert r["elements"] in ("quote", "concept")
    assert r["levels"]["concept"]["n"] == 15


def test_analyst_dry_run_and_process_metrics(project):
    consolidate(project, code_corpus(project, "code1", log=lambda *_: None).id, into="cons", use_model=False, log=lambda *_: None)
    runs = run_pi(project, "cons", replicates=2, dry_run=True, log=lambda *_: None)
    assert [r.id for r in runs] == ["cons-pi-1", "cons-pi-2"]
    ws = runs[0].dir / "workspace" / "qls"
    assert ws.exists() and "QLS_RUN=\"cons-pi-1\"" in ws.read_text()
    assert runs[0].state()["themes"] == {}
    events = [
        {"type": "turn_start"},
        {"type": "tool_execution_start", "toolName": "bash",
         "args": {"command": "qls concepts --cards && qls doc read P01 --from 3; qls search 'cloud'"}},
        {"type": "tool_execution_start", "toolName": "bash",
         "args": {"command": "qls theme create --label 'x y' --concepts c1 --reason 'r' && qls memo 'hi'"}},
        {"type": "tool_execution_start", "toolName": "read", "args": {"path": "x"}},
    ]
    (runs[0].dir / "pi-events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    m = process_metrics(runs[0])
    assert m["turns"] == 1 and m["tool_calls"] == 3 and m["file_reads"] == 1
    assert m["qls_reads"] == {"concepts": 1, "doc": 1, "search": 1}
    assert m["qls_edits"] == {"theme create": 1, "memo": 1}
    assert m["docs_read"] == ["P01"]


def test_mock_llm_is_deterministic(project):
    llm = MockLLM({"provider": "mock"})
    a = code_corpus(project, "m1", llm=llm, log=lambda *_: None).state()["concepts"]
    b = code_corpus(project, "m2", llm=llm, log=lambda *_: None).state()["concepts"]
    assert [c["label"] for c in a.values()] == [c["label"] for c in b.values()]
