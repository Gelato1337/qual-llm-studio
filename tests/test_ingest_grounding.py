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
    long = "I: Q?\nR: ok.\nI: More?\nR: " + " ".join(f"Sentence number {i} is here." for i in range(200))
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


DOCLING_LIKE = """<!-- image -->

## Oral History of Someone

William Aspray: Today is Friday and I am interviewing Kapor.

## Background and Education

Aspray: Tell me about your education.

## Kapor: Well, so where to start?

## Aspray:

## Education.

Kapor:

I studied psychology and then taught meditation.

## Aspray: What did your father do?

## Kapor:

He was in business, small manufacturing.
"""


def test_converter_markdown_labels_sections_and_full_names():
    doc = build_document("k", DOCLING_LIKE, [], 2000)
    assert doc["speakers"] == {"Aspray": "interviewer", "Kapor": "informant"}
    texts = [s["text"] for s in doc["segments"]]
    assert texts == ["Well, so where to start?", "I studied psychology and then taught meditation.",
                     "He was in business, small manufacturing."]
    assert not any("father" in t for t in texts)  # interviewer questions never leak into informant text
    assert doc["segments"][2]["question"] == "What did your father do?"
    assert doc["segments"][0]["section"] == "Background and Education"
    assert doc["segments"][1]["section"] == "Education."
    assert doc["turns"][0]["role"] == "meta"
    assert any(t["speaker"] == "section" for t in doc["turns"])
    first_q = [t for t in doc["turns"] if t["role"] == "interviewer"][0]
    assert first_q["text"].startswith("Today is Friday")  # "William Aspray:" resolved to Aspray


def test_page_break_inside_sentence_is_not_a_segment_boundary():
    long = "I: Why?\nR: Fine.\nI: Go on?\nR: " + "First part of a long answer. " * 40 + "I had to leave school because I knew I\n\nwasn't done. " + "More text here. " * 40
    doc = build_document("x", long, ["I"], 800)
    assert any("because I knew I\n\nwasn't done." in s["text"] for s in doc["segments"])  # kept together
    turn = next(t for t in doc["turns"] if t["text"].startswith("First part"))
    assert all(s["text"] in turn["text"] for s in doc["segments"][1:])  # segments are exact slices
    assert [s["question_gap"] for s in doc["segments"]] == [0] + list(range(len(doc["segments"]) - 1))


def test_page_footers_are_dropped():
    page = "CHM Ref:X.2004      © 2004 Computer History Museum      Page {} of 9"
    text = "\n".join([
        "Haigh: Why spreadsheets?",
        "Fylstra: Because accountants",
        page.format(3),
        "already worked in rows and columns.",
        "Haigh: And then?", "Fylstra: We sold it.", page.format(4),
        "Haigh: Where?", "Fylstra: Everywhere.", page.format(5),
    ])
    doc = build_document("F", text, ["Haigh"], 2000)
    assert doc["speakers"] == {"Haigh": "interviewer", "Fylstra": "informant"}
    assert doc["segments"][0]["text"] == "Because accountants\nalready worked in rows and columns."


def test_package_data():
    from qls.method import builtin_methods, core_schema, load_method

    assert "gioia" in builtin_methods()
    assert "entity intent" in core_schema()
    m = load_method("gioia")
    assert m.levels() == ["concept", "theme", "dimension"] and "Gioia" in m.guide
    from importlib import resources

    assert "Quotes" in resources.files("qls.agents").joinpath("AGENTS.md").read_text(encoding="utf-8")
