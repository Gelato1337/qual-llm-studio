from pathlib import Path

import pytest

from qls.ingest import ingest_file
from qls.project import Project

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "synthetic"


@pytest.fixture
def project(tmp_path) -> Project:
    p = Project.init(tmp_path / "study")
    cfg = (p.root / "qls.toml").read_text()
    (p.root / "qls.toml").write_text(cfg.replace('provider = "anthropic"', 'provider = "mock"', 1))
    p = Project(p.root)
    for f in sorted(EXAMPLES.glob("*.txt")):
        ingest_file(p, f)
    return p
