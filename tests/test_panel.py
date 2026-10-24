"""sim-agent as an EWALD workflow source, and its side bar (EWALD's workflow side bar) with a
stand-in for the window (no MainWindow, no display)."""
import os

import pytest


def _names(step):
    return [s.name for s in step.steps]


def test_listing_rows(connected):
    from ewald_simagent.source import SimAgentWorkflows

    src = SimAgentWorkflows()
    assert src.account().startswith("alice at ")
    page = src.list()
    titles = [w.title for w in page.workflows]
    assert titles[0].startswith("vasp_mlff_training") and "<400>" in titles[0]
    assert "vasp_bands  Si2  <100>  ✓" in titles and not page.workflows[0].complete
    assert [w.title for w in src.list(query="bands").workflows] == ["vasp_bands  Si2  <100>  ✓"]


def test_listing_rows_carry_the_chat_name(connected):
    from ewald_simagent.source import SimAgentWorkflows

    src = SimAgentWorkflows()
    groups = {w.id: w.group for w in src.list().workflows}
    assert groups["100"] == "Si band structure"
    assert groups["400"] == "chat 7b1d0e44"       # the chat was deleted: its id stays
    assert groups["300"] == ""                    # no chat (an older server, or not from a chat)
    assert [w.id for w in src.list(query="band struct").workflows] == ["100"]
    assert src.describe("100").group == "Si band structure"


def test_describe_joins_restarts_and_nests_call_paths(connected):
    from ewald_simagent.source import SimAgentWorkflows

    wf = SimAgentWorkflows().describe("100")
    assert wf.complete and wf.path == "simagent:default/00000064-" and _names(wf) == ["relax", "scf", "bands"]
    relax = wf.steps[0]
    assert relax.runs == 2 and relax.mark() == "✓" and relax.path == "simagent:default/00000064-/relax"
    chg = next(o for o in relax.outputs if o.name == "CHGCAR")
    assert chg.note == "on the cluster, over SSH" and chg.path.endswith("/relax/CHGCAR")
    assert [o.name for o in wf.outputs] == ["relaxed_structure"] and wf.scalars["band_gap"] == 0.61
    mlff = SimAgentWorkflows().describe("simagent:400")
    assert _names(mlff) == ["dft"] and _names(mlff.steps[0]) == ["SiO2_000", "SiO2_001", "Si_000"]
    assert mlff.steps[0].steps[1].runs == 2


def test_without_login():
    from ewald_simagent.source import SimAgentWorkflows

    assert SimAgentWorkflows().account() == ""


def test_side_bar_is_ewalds_workflow_panel(connected):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ewald import plugins

    if "simagent" not in plugins.workflow_sources():
        pytest.skip("this EWALD has no workflow sources")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841

    class Context:
        def __init__(self):
            self.opened, self.workflows, self.cells, self.window = [], [], [], self

        def open(self, path):
            self.opened.append(path)

        def open_workflow(self, wf):
            self.workflows.append(wf)

        def insert_cell(self, code):
            self.cells.append(code)

        def message(self, text, error=False):
            pass

        def on(self, event, callback):
            pass

    from ewald_simagent.panel import sim_agent

    ctx = Context()
    panel = sim_agent(ctx)
    panel.wait()
    wg = next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
              if "<100>" in panel.tree.topLevelItem(i).text(0))
    wg.setExpanded(True)
    panel.wait()
    texts = [wg.child(i).text(0) for i in range(wg.childCount())]
    assert texts[:3] == ["relax  (2 runs)  ✓", "scf  ✓", "bands  ✓"] and "relaxed_structure  (Si2)" in texts
    panel.tree.setCurrentItem(wg.child(0))
    panel.open()
    panel.insert_cell()
    assert ctx.opened == ["simagent:default/00000064-/relax"]
    assert ctx.cells == ["pipe = load('simagent:default/00000064-/relax')"]
    panel.open(wg)
    assert [w.id for w in ctx.workflows] == ["100"]


def test_panel_is_registered():
    from ewald import plugins

    p = plugins.panels()["simagent"]
    assert p.title == "sim-agent" and p.icon.startswith("<svg")
