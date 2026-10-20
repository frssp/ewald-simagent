"""The sim-agent side bar, with a stand-in for EWALD's window (no MainWindow, no display)."""
import os

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class Context:
    def __init__(self):
        self.opened, self.cells, self.messages, self.callbacks = [], [], [], {}
        self.window = self

    def open_location_dialog(self, path):
        self.opened.append(path)
        return path

    def insert_cell(self, code):
        self.cells.append(code)

    def message(self, text, error=False):
        self.messages.append(text)

    def on(self, event, callback):
        self.callbacks.setdefault(event, []).append(callback)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _texts(item):
    return [item.child(i).text(0) for i in range(item.childCount())]


def test_panel_lists_expands_and_opens(app, connected):
    from ewald_simagent.panel import sim_agent

    ctx = Context()
    panel = sim_agent(ctx)
    panel.wait()
    rows = [panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())]
    assert rows[0].startswith("vasp_mlff_training") and "<400>" in rows[0]
    assert "vasp_bands  Si2  <100>  ✓" in rows and "alice at" in panel.who.text()
    wg = next(panel.tree.topLevelItem(i) for i in range(3) if "<100>" in panel.tree.topLevelItem(i).text(0))
    wg.setExpanded(True)
    panel.wait()
    assert _texts(wg)[:3] == ["relax  (2 runs)  ✓", "scf  ✓", "bands  ✓"] and "relaxed_structure  Si2" in _texts(wg)
    relax = wg.child(0)
    assert "CHGCAR  (on the cluster, over SSH)" in _texts(relax)
    panel.tree.setCurrentItem(relax)
    assert panel.buttons["Open"].isEnabled()
    panel.open()
    panel.insert_cell()
    assert ctx.opened == ["simagent:default/00000064-/relax"]
    assert ctx.cells == ["pipe = load('simagent:default/00000064-/relax')"]


def test_panel_search_and_pk(app, connected):
    from ewald_simagent.panel import sim_agent

    panel = sim_agent(Context())
    panel.wait()
    panel.search.setText("bands")
    panel.refresh()
    panel.wait()
    assert panel.tree.topLevelItemCount() == 1
    panel.search.setText("400")
    panel.refresh()
    panel.wait()
    top = panel.tree.topLevelItem(0)
    assert "<400>" in top.text(0) and _texts(top) == ["dft/SiO2_000  ✓", "dft/SiO2_001  (2 runs)  ✓", "dft/Si_000  ✓"]


def test_panel_without_login(app):
    from ewald_simagent.panel import sim_agent

    panel = sim_agent(Context())
    assert "Not logged in" in panel.who.text() and panel.tree.topLevelItemCount() == 0
    assert panel.login_button.text() == "Log in…"


def test_panel_is_registered():
    from ewald import plugins

    p = plugins.panels().get("simagent")
    if p is None or p.title == "simagent":
        pytest.skip("this EWALD does not read panel attributes from entry points yet")
    assert p.title == "sim-agent" and p.icon.startswith("<svg")
