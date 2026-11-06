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


def _archive_requests():
    from .conftest import Handler

    return [r for r in Handler.requests if "archive.aiida" in r]


def test_a_finished_workflow_is_the_archives_tree_read_once(source):
    wf = source.describe("600")
    steps = {s.name: s for _, s in wf.walk()}
    static = steps["slab_111_t1_static"]
    assert static.shape == {"kind": "static", "flags": ["slab", "potential"]}
    assert static.id == "602" and static.uuid == f"{602:08x}-0000-4000-8000-000000000000"   # the server's PK
    assert static.path.endswith("/slab_111_t1_static") and static.path.startswith("simagent:")
    assert {o.name for o in static.outputs} >= {"vasprun.xml", "locpot_planar_avg.json", "CHGCAR"}
    assert static.as_dict()["shape"]["kind"] == "static"       # describe_workflow sees it
    assert _archive_requests() == ["/alice/api/v1/nodes/600/archive.aiida?content=metadata"]
    source.describe("600")
    assert len(_archive_requests()) == 1          # a finished workflow's archive is cached


def test_nested_steps_and_restarts_come_from_the_archive(source):
    mlff = source.describe("400")
    assert [s.name for s in mlff.steps] == ["dft"]
    assert [s.name for s in mlff.steps[0].steps] == ["SiO2_000", "SiO2_001", "Si_000"]
    assert mlff.steps[0].steps[1].runs == 2 and mlff.steps[0].steps[1].id == "403"
    assert mlff.steps[0].steps[1].path.endswith("/dft/SiO2_001")


def test_a_new_template_gets_the_work_function_by_its_steps_shapes(source):
    wf = source.describe("700")
    (a,) = wf.analyses
    assert a.available and a.name == "work_function"
    nb = Notebook.from_py(workflow.notebook_text(wf), name="work function")
    assert nb.run_all(), [c.outputs for c in nb.cells if c.state == "error"]
    table = nb.namespace["work_function"]
    assert table.labels == ["surface_a_scf", "surface_b_scf"]
    assert list(table["Work function (eV)"]) == pytest.approx([PHI["111_t1"], PHI["100_t1"]], abs=1e-6)


def test_a_new_template_gets_the_lineup_by_its_structures_roles(source):
    wf = source.describe("800")
    (a,) = wf.analyses
    assert a.available and a.name == "lineup"
    nb = Notebook.from_py(workflow.notebook_text(wf), name="junction")
    assert nb.run_all(), [c.outputs for c in nb.cells if c.state == "error"]
    res = nb.namespace["band_offset"].attributes["result"]
    assert res["schottky_barrier_p_eV"] == pytest.approx(PHI_BP, abs=0.01)


def test_without_an_archive_the_calculations_are_listed_one_by_one(source, monkeypatch):
    wf = source.describe("500")                   # no archive on this server
    assert [s.name for s in wf.steps] == ["interface_relax", "interface_static", "ref_substrate_static",
                                          "ref_film_static"]
    assert wf.steps[1].id == "502" and wf.steps[1].shape == {}
    assert wf.analyses[0].available               # the recipe goes by the task names

    def missing(path):
        raise ImportError("ewald_aiida")

    monkeypatch.setattr("ewald_simagent.archive._open_archive", missing)
    wf = source.describe("700")
    assert [s.name for s in wf.steps] == ["bulk_relax", "surface_a_scf", "surface_b_scf"]
    assert wf.analyses == []                      # no shapes, and names no recipe knows


def test_a_running_workflow_lists_its_calculations_without_asking_for_an_archive(source):
    from .conftest import NODES

    NODES[100]["process_state"] = "running"
    calcs = NODES[100]["calculations"]
    calcs[-1]["process_state"], files = "running", calcs[-1]["files"]
    calcs[-1]["files"] = []                       # retrieved only when it finishes
    try:
        wf = source.describe("100")
        assert [(s.name, s.runs, s.state) for s in wf.steps] == [
            ("relax", 2, "finished"), ("scf", 1, "finished"), ("bands", 1, "running")]
        assert wf.steps[0].path.endswith("/relax")           # a finished calculation opens on its own
        assert wf.steps[2].path == "" and not any(o.name == "vasprun.xml" for o in wf.steps[2].outputs)
        assert not _archive_requests()
    finally:
        NODES[100]["process_state"], calcs[-1]["process_state"], calcs[-1]["files"] = "finished", "finished", files
    assert source.describe("300").steps == [] and not _archive_requests()   # a calculation itself
