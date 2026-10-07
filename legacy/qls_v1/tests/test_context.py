import pytest

from qls.coding import code_corpus, consolidate
from qls.compare import compare_runs
from qls.grouping import concept_listing, group, oneshot
from qls.metrics import run_metrics
from qls.ops import Ops
from qls.project import QlsError
from qls.runs import fork
from qls.views import check, concept_card, context_pack, memos_about

quiet = {"log": lambda *_: None}


@pytest.fixture
def cons(project):
    code_corpus(project, "code1", **quiet)
    return consolidate(project, "code1", into="cons", use_model=False, **quiet)


def test_coding_memos_stored_and_switchable(project):
    s = code_corpus(project, "m", **quiet).state()
    assert all(c.get("memo", {}).get("meaning_here") for c in s["concepts"].values())
    cfg = project.root / "qls.toml"
    cfg.write_text(cfg.read_text().replace("concept_memos = true", "concept_memos = false"))
    from qls.project import Project

    p2 = Project(project.root)
    s2 = code_corpus(p2, "nm", **quiet).state()
    assert not any(c.get("memo") for c in s2["concepts"].values())
    assert p2.run("nm").manifest()["concept_memos"] is False


def test_memos_survive_merges_and_evidence_is_checked(project, cons):
    r = fork(project, "cons", "g", keep="concepts")
    ops = Ops(r, actor="agent:t")
    ops.add_memo("not about price", ["c2"], kind="boundary", evidence=["P01:s002"])
    new = ops.merge_concepts(["c2", "c7"], "Faster releases", "d", "same point")
    s = r.state()
    card = concept_card(project, s, new)
    assert {m["concept"] for m in card["coding_memos"]} == {"c2", "c7"}  # carried through the merge
    assert [m["text"] for m in card["memos"]] == ["not about price"]
    with pytest.raises(QlsError):
        ops.add_memo("x", ["c999"])
    with pytest.raises(QlsError):
        ops.add_memo("x", ["c3"], kind="feeling")
    ops.with_evidence(["P01:s002", "q2"]).create_theme("Delivery", "", [new], "same pressure")
    assert r.decisions()[-1]["evidence"] == ["P01:s002", "q2"]
    with pytest.raises(QlsError):
        ops.with_evidence(["P99:s001"])
    assert memos_about(r.state(), new)


def test_context_pack_rebuilds_conversation(project, cons):
    r = fork(project, "cons", "g", keep="concepts")
    Ops(r, "a").create_theme("Delivery", "def", ["c2", "c7"], "r")
    s = r.state()
    pack = context_pack(project, s, ["c2"])
    assert ">> P01 (informant)" in pack and "I (interviewer): What changes" in pack and "coding memo" in pack
    tpack = context_pack(project, s, ["t1", "P02:s002"])
    assert "theme t1: Delivery" in tpack and "coded as: c7" in tpack
    assert "cut at" in context_pack(project, s, ["t1", "c2", "c7"], max_chars=300)
    with pytest.raises(QlsError):
        context_pack(project, s, ["zz"])


def test_views_differ_in_what_the_model_sees(project, cons):
    s = cons.state()
    labels, cards, memos = (concept_listing(project, s, v) for v in ("labels", "cards", "memos"))
    assert "quote" not in labels and "quote" in cards and "coding memo" not in cards and "coding memo" in memos


def test_group_verify_oneshot_and_metrics(project, cons):
    for view in ("labels", "memos"):
        r = group(project, "cons", view=view, **quiet)
        assert check(r) == [] and r.manifest()["view"] == view
    m = run_metrics(project, "cons-group-memos-v")
    assert m["themes"] > 0 and m["coverage"] == 1.0 and 0 <= m["theme_generic_share"] <= 1
    one = oneshot(project, "one", **quiet)
    assert one.manifest()["grounding"]["quotes_failed"] == 0
    cmp = compare_runs(project, "one", "cons-group-memos-v")
    assert cmp["elements"] == "quote"  # independent codings compared on quoted text


def test_leave_one_informant_out(project, cons):
    r = fork(project, "cons", "loo", keep="concepts")
    res = Ops(r, "a").exclude_documents(["P02"], "leave out P02")
    s = r.state()
    assert res["quotes_removed"] == 5
    assert not any(q["doc"] == "P02" for q in s["quotes"].values())
    assert check(r) == []
    assert all(s["concepts"][c]["status"] == "excluded" for c in res["concepts_excluded"])
