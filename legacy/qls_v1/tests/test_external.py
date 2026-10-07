import json
import re

import pytest

from qls.coding import code_corpus, consolidate, llm_config
from qls.grouping import group
from qls.llm import MockLLM, PendingAnswers, make_llm
from qls.views import check

quiet = {"log": lambda *_: None}


def answer_all(project):
    """Stand-in answerer: answers every pending request the way the mock model would."""
    mock = MockLLM({"provider": "mock"})
    d = project.root / "external"
    n = 0
    for req in (d / "requests").glob("*.md"):
        ans = d / "answers" / f"{req.stem}.json"
        if ans.exists():
            continue
        text = req.read_text()
        name = req.stem.rsplit("-", 1)[0]
        schema = json.loads(re.search(r"```json\n(.*)\n```", text, re.S).group(1))
        user = text.split("## Input\n\n", 1)[1].split("\n\n## JSON schema", 1)[0]
        ans.write_text(json.dumps(mock.json("", user, schema, name).data))
        n += 1
    return n


def test_external_round_trip(project):
    llm = make_llm(llm_config(project, provider="external", model="external:test"))
    with pytest.raises(PendingAnswers) as e:
        code_corpus(project, "c", llm=llm, **quiet)
    assert len(e.value.requests) == 3  # one per transcript, collected in one pass
    assert project.run("c").manifest()["status"] == "waiting"
    assert answer_all(project) == 3
    run = code_corpus(project, "c", llm=llm, **quiet)  # same name: the waiting run is replaced
    assert run.manifest()["status"] == "done" and len(run.state()["concepts"]) == 15
    assert run.manifest()["served_models"] == ["external:test"]

    with pytest.raises(PendingAnswers):
        consolidate(project, "c", into="k", llm=llm, **quiet)
    answer_all(project)
    consolidate(project, "c", into="k", llm=llm, **quiet)

    rounds = 0
    while True:
        try:
            g = group(project, "k", into="g", view="memos", llm=llm, **quiet)
            break
        except PendingAnswers:
            rounds += 1
            assert answer_all(project) > 0
    assert rounds == 3  # grouping, then all theme checks at once, then all dimension checks
    assert g.manifest()["status"] == "done" and check(g) == []
