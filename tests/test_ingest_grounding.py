from qls.grounding import ground, locate
from qls.ingest import build_document, parse_turns

TRANSCRIPT = """Interview 3, recorded online.

**Interviewer:** What changed in delivery?
**P3:** Clients expect releases every two weeks now.
Before, a yearly release was normal.

**Interviewer:** And the cloud?
**P3:** We are locked in to one vendor.
"""


def test_speakers_roles_and_preamble():
    doc = build_document("P3", TRANSCRIPT, ["I"], 2000)
    assert doc["speakers"] == {"Interviewer": "interviewer", "P3": "informant"}
    roles = [t["role"] for t in doc["turns"]]
    assert roles[0] == "meta"  # preamble is kept but never coded
    assert len(doc["segments"]) == 2
    s1 = doc["segments"][0]
    assert s1["id"] == "P3:s001"
    assert "yearly release" in s1["text"]  # continuation line joins the turn
    assert s1["question"] == "What changed in delivery?"


def test_unlabelled_text_is_informant_paragraphs():
    turns = parse_turns("First paragraph.\n\nSecond paragraph.")
    assert turns == [(None, "First paragraph."), (None, "Second paragraph.")]


def test_interviewer_found_by_questions_when_unlabelled_role():
    text = "A: How are you?\nB: Fine, thanks, busy with work.\nA: Why busy?\nB: Deadlines everywhere."
    doc = build_document("x", text, [], 2000)
    assert doc["speakers"] == {"A": "interviewer", "B": "informant"}


def test_long_turn_is_split():
    long = "I: Q?\nR: " + " ".join(f"Sentence number {i} is here." for i in range(200))
    doc = build_document("x", long, ["I"], 500)
    assert len(doc["segments"]) > 3
    assert all(len(s["text"]) <= 520 for s in doc["segments"])


def test_locate_exact_normalized_fuzzy_and_miss():
    text = "We are locked in to   one vendor, and moving costs more than building."
    assert locate("locked in to   one vendor", text).method == "exact"
    sp = locate("Locked in to one vendor", text)
    assert sp.method == "normalized" and text[sp.start:sp.end].lower().startswith("locked")
    sp = locate("moving cost more than building", text, threshold=85)
    assert sp.method == "fuzzy" and sp.score >= 85
    assert locate("we love open source", text) is None


def test_ground_falls_back_to_other_segment():
    segs = [{"id": "d:s001", "text": "alpha beta gamma"}, {"id": "d:s002", "text": "the vendor lock-in worries us"}]
    seg, sp = ground("vendor lock-in worries us", "d:s001", segs)
    assert seg["id"] == "d:s002" and sp.score == 100
