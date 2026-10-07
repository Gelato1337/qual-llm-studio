"""The ontology's rules hold in TypeDB itself. Needs a TypeDB 3 server: QLS_TYPEDB=127.0.0.1:1729."""

import os
from pathlib import Path

import pytest

ADDR = os.environ.get("QLS_TYPEDB")
pytestmark = pytest.mark.skipif(not ADDR, reason="set QLS_TYPEDB to a TypeDB 3 server")
SCHEMA = Path(__file__).resolve().parent.parent / "schema"


@pytest.fixture(scope="module")
def db():
    from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TransactionType, TypeDB

    drv = TypeDB.driver(ADDR, Credentials("admin", os.environ.get("QLS_TYPEDB_PASSWORD", "password")),
                        DriverOptions(DriverTlsConfig.disabled()))
    name = "qls_schema_test"
    if drv.databases.contains(name):
        drv.databases.get(name).delete()
    drv.databases.create(name)

    def q(query, kind=TransactionType.WRITE):
        with drv.transaction(name, kind) as tx:
            ans = tx.query(query).resolve()
            rows = list(ans.as_concept_rows()) if kind == TransactionType.READ else None
            if kind != TransactionType.READ:
                tx.commit()
            return rows

    for f in ("core.tql", "methods/gioia.tql"):
        q((SCHEMA / f).read_text(), TransactionType.SCHEMA)
    q('insert $s isa source, has id "P01", has version 1;'
      + "".join(f' $q{i} isa quote, has id "Q-{i}", has start-offset {i}, has end-offset {i + 5}, has unit "P01:s00{i}";'
                f' quoting (source: $s, quote: $q{i});' for i in range(1, 5)))
    for i in range(1, 5):
        q(f'match $q isa quote, has id "Q-{i}"; insert $c isa concept, has id "C-{i}", has label "c{i}", has status "active";'
          f' $e isa evidence (claim: $c, quote: $q), has reason "says it";')
    q.read = lambda s: q(s, TransactionType.READ)
    yield q
    drv.databases.get(name).delete()
    drv.close()


def refused(q, query):
    try:
        q(query)
    except Exception as exc:  # TypeDBDriverException
        return str(exc)
    return None


def test_concept_needs_quote_with_reason(db):
    assert "@card(1..)" in refused(db, 'insert $c isa concept, has id "C-9", has label "x";')
    assert "reason" in refused(db, 'match $q isa quote, has id "Q-1"; insert $c isa concept, has id "C-9", has label "x";'
                                   ' evidence (claim: $c, quote: $q);')
    assert refused(db, 'match $c isa concept, has id "C-1"; $e isa evidence (claim: $c); delete $e;')


def test_memo_must_be_about_something(db):
    assert refused(db, 'insert $m isa memo, has id "M-9", has memo-kind "meaning", has body "x";')
    assert refused(db, 'match $c isa concept, has id "C-1"; insert $m isa memo, has id "M-9", has memo-kind "feeling",'
                       ' has body "x"; about (memo: $m, target: $c);')
    assert not refused(db, 'match $c isa concept, has id "C-1"; insert $m isa memo, has id "M-1", has memo-kind "uncertainty",'
                           ' has body "x"; about (memo: $m, target: $c);')


def test_gioia_hierarchy(db):
    assert refused(db, 'match $a isa concept, has id "C-1"; insert $t isa theme, has id "T-9", has label "t";'
                       ' $m isa theme-membership (theme: $t, concept: $a), has reason "r";')
    assert not refused(db, 'match $a isa concept, has id "C-1"; $b isa concept, has id "C-2";'
                           ' insert $t isa theme, has id "T-1", has label "t";'
                           ' $m isa theme-membership (theme: $t, concept: $a), has reason "r";'
                           ' $n isa theme-membership (theme: $t, concept: $b), has reason "r";')
    assert refused(db, 'match $a isa concept, has id "C-1"; $b isa concept, has id "C-3";'
                       ' insert $t isa theme, has id "T-2", has label "t";'
                       ' $m isa theme-membership (theme: $t, concept: $a), has reason "r";'
                       ' $n isa theme-membership (theme: $t, concept: $b), has reason "r";')  # C-1 already placed
    assert not refused(db, 'match $t isa theme, has id "T-1"; insert $d isa dimension, has id "D-1", has label "d";'
                           ' $m isa dimension-membership (dimension: $d, theme: $t), has reason "r";')
    rows = db.read('match $d isa dimension, has id "D-1"; let $q in dimension_quotes($d); $q has id $i; select $i;')
    assert sorted(r.get("i").get_value() for r in rows) == ["Q-1", "Q-2"]
    rows = db.read('match $x isa code, has id $i; select $i;')  # method-agnostic view
    assert len(rows) == 4
