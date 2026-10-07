import asyncio
import os

import pytest

from qls.cli import main


def run(args, project):
    main(["--help"] if args is None else args + (["--project", str(project.root)] if "--project" not in args else []))


def test_cli_flow(project, capsys, monkeypatch):
    monkeypatch.setenv("QLS_ACTOR", "agent:cli-test")
    root = str(project.root)
    main(["code", "--run", "c1", "--project", root])
    main(["fork", "c1", "g", "--blind", "--project", root])
    main(["theme", "create", "--label", "Delivery", "--concepts", "c2", "c7", "--reason", "same", "--run", "g", "--project", root])
    main(["dim", "create", "--label", "Change", "--themes", "t1", "--reason", "x", "--run", "g", "--project", root])
    main(["memo", "a note", "--link", "t1", "--run", "g", "--project", root])
    main(["structure", "--run", "g", "--project", root])
    out = capsys.readouterr().out
    assert "a1: Change" in out and "t1: Delivery" in out
    main(["report", "--run", "g", "--project", root])
    main(["export", "--run", "g", "--project", root])
    assert (project.root / "reports" / "g.html").exists()
    assert (project.root / "exports" / "g" / "structure.csv").exists()
    with pytest.raises(SystemExit):
        main(["theme", "create", "--label", "x", "--concepts", "c999", "--reason", "r", "--run", "g", "--project", root])
    assert "Unknown concept" in capsys.readouterr().err
    main(["guide"])
    assert "qls theme create" in capsys.readouterr().out


def test_mcp_server_tools(project):
    pytest.importorskip("mcp")
    from qls import mcp_server

    mcp_server._state["project"] = project
    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"open_project", "list_concepts", "create_theme", "add_memo", "compare_runs", "write_report"} <= names
