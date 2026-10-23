"""EWALD's own AI tools on sim-agent rows: list_workflows' filters (Hill formulas, sim-agent's
states and creation times) and open_workflow down to a step."""
from datetime import datetime, timedelta, timezone

import pytest
from ewald import workflow

from .conftest import NODES

pytestmark = pytest.mark.skipif(not hasattr(workflow, "find"), reason="needs an EWALD with workflow.find")

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=timezone(timedelta(hours=9)))


@pytest.fixture
def tin(monkeypatch):
    def node(pk, ctime, formula, exit_status=0):
        return {"schema": "sim-agent.node/1", "pk": pk, "uuid": f"{pk:08x}-2222-4000-8000-000000000000",
                "node_type": "", "label": "", "ctime": ctime, "kind": "process", "template": "vasp_relax",
                "process_label": "WorkGraph<vasp_relax>", "formula": formula, "process_state": "finished",
                "exit_status": exit_status, "scalars": {}, "structures": [], "calculations": []}
    monkeypatch.setitem(NODES, 501, node(501, "2026-10-21T03:00:00+00:00", "N4Ti4"))
    monkeypatch.setitem(NODES, 502, node(502, "2026-10-22T01:00:00+00:00", "N31Ti32"))
    monkeypatch.setitem(NODES, 503, node(503, "2026-10-22T02:00:00+00:00", "N4Ti4", exit_status=508))


def test_finished_yesterday_tin(connected, tin):
    found = workflow.find("simagent", formula="TiN", status_="succeeded", since="yesterday", until="yesterday",
                          now=NOW)
    assert [w.id for w in found["workflows"]] == ["502"]
    assert [w.id for w in workflow.find("simagent", formula="TiN", status_="failed", now=NOW)["workflows"]] == ["503"]


def test_open_workflow_step(connected):
    from ewald.llm.tools import Toolset

    ts = Toolset()
    out = ts.call("open_workflow", {"path": "simagent:100", "step": "bands"})
    assert not out.is_error, out.content
    assert out.content["path"].endswith("/bands") and out.content["loaded"]["num_frames"] >= 1
    bad = ts.call("open_workflow", {"path": "simagent:100", "step": "band"})
    assert bad.is_error and "steps: bands, relax, scf" in bad.content
