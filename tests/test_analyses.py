"""Analyses of sim-agent workflows as notebook cells: the band-offset lineup end to end, from the
fake backend's made-up run (tests/band_offset_data.py) to the barriers it was built with."""
import pytest
from ewald import plugins, workflow
from ewald.notebook import Notebook

from ewald_simagent.analyses import analyses
from ewald_simagent.source import SimAgentWorkflows

from .band_offset_data import PHI_BN, PHI_BP
from .workfunction_data import PHI


@pytest.fixture
def source(connected):
    plugins.workflow_source("simagent", schemes=("simagent",))(SimAgentWorkflows)
    return plugins.workflow_sources()["simagent"]


def test_band_offset_lineup_runs_in_a_notebook(source):
    wf = source.describe("500")
    assert [a.as_dict() for a in wf.analyses] == [
        {"name": "lineup", "title": "Potential lineup: band offset / Schottky barrier", "available": True}]
    nb = Notebook.from_py(workflow.notebook_text(wf), name="band offset")
    assert nb.run_all(), [c.outputs for c in nb.cells if c.state == "error"]
    table = nb.namespace["band_offset"]
    res = table.attributes["result"]
    assert res["junction"] == "metal/semiconductor" and res["semiconductor_side"] == "film"
    assert res["schottky_barrier_p_eV"] == pytest.approx(PHI_BP, abs=0.01)
    assert res["schottky_barrier_n_eV"] == pytest.approx(PHI_BN, abs=0.01)
    levels = {lv["name"]: lv["energy"] for lv in table.attributes["levels"]}
    assert levels["E_F (interface)"] == pytest.approx(0.0)
    assert levels["O2Ti VBM"] == pytest.approx(-PHI_BP, abs=0.01)


def test_the_ai_adds_the_lineup_and_gets_the_numbers(source):
    from ewald.llm.tools import Toolset

    out = Toolset().call("add_analysis", {"path": "simagent:500", "name": "lineup"}).content
    assert out["result"]["schottky_barrier_p_eV"] == pytest.approx(PHI_BP, abs=0.01)
    assert out["energy_axis"] == "E - E_F (interface)"


def test_a_workflow_without_the_statics_gets_a_reason_not_a_guess(source):
    wf = source.describe("500")
    wf.steps = [s for s in wf.steps if s.name != "ref_film_static"]
    wf.analyses = []
    (a,) = analyses(wf)
    assert not a.available and "ref_film_static" in a.reason
    assert analyses(source.describe("100")) == []     # vasp_bands: nothing to add



def test_work_function_per_surface_runs_in_a_notebook(source):
    wf = source.describe("600")
    (a,) = wf.analyses
    assert a.available and a.name == "work_function"
    nb = Notebook.from_py(workflow.notebook_text(wf), name="work function")
    assert nb.run_all(), [c.outputs for c in nb.cells if c.state == "error"]
    table = nb.namespace["work_function"]
    assert table.labels == ["111_t1", "100_t1"]
    assert list(table["Work function (eV)"]) == pytest.approx([PHI["111_t1"], PHI["100_t1"]], abs=1e-6)
    assert table.attributes["skipped"] == ["110_t1"]      # no planar average: named, not dropped silently
    prof = nb.namespace["profiles"]["111_t1"]
    assert prof.attributes["energy_axis"] == "E - E_vac"


def test_the_ai_gets_the_work_functions(source):
    from ewald.llm.tools import Toolset

    out = Toolset().call("add_analysis", {"path": "simagent:600", "name": "work_function"}).content
    assert out["result"]["100_t1"]["work_function_eV"] == pytest.approx(PHI["100_t1"], abs=1e-6)
