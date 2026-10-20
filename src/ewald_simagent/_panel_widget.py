"""The Qt side of the sim-agent side bar (:mod:`ewald_simagent.panel`).

Top: who you are logged in as, and a button to the login page. Then a box that takes a PK or
UUID (that calculation alone) or words (a search of your calculations). Below, your recent
calculations, newest first; expanding one asks sim-agent what it holds and lists its steps (with
their retrieved files) and output structures. A double click opens an item, as File › Open
location would; the buttons insert a ``load(...)`` cell instead or copy the path. Requests run
off the GUI thread, so a slow server never freezes the window.
"""
from __future__ import annotations

import re
from collections import OrderedDict

from PySide6 import QtCore, QtWidgets

from . import client
from .resolve import TRAJECTORY_FILE, run_key
from .uri import stable

PATH = QtCore.Qt.UserRole          # the simagent: path an item opens
NODE = QtCore.Qt.UserRole + 1      # a calculation's pk, on top-level items not yet expanded
_NODE_TEXT = re.compile(r"^(\d+|[0-9a-fA-F-]{8,36})$")
#: files read from the work directory on the cluster when asked for (not retrieved by AiiDA)
CLUSTER_FILES = ("CHGCAR", "LOCPOT", "ELFCAR")
STATE = {"finished": "✓", "excepted": "✗", "killed": "✗", "running": "…", "waiting": "…", "created": "…"}


class _Job(QtCore.QThread):
    """``fn()`` on another thread; ``done(result, error)`` back on the GUI thread."""

    done = QtCore.Signal(object, str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), "")
        except client.SimAgentError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001 (a plugin bug shows in the panel, not as a crash)
            self.done.emit(None, f"{type(exc).__name__}: {exc}")


def _row_text(n: dict) -> str:
    what = n.get("template") or n.get("process_label") or "calculation"
    bits = [what, n.get("formula") or "", f"<{n['pk']}>", STATE.get(n.get("process_state"), "")]
    if n.get("exit_status") not in (None, 0):
        bits.append(f"exit {n['exit_status']}")
    return "  ".join(b for b in bits if b)


class SimAgentPanel(QtWidgets.QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.jobs: list[_Job] = []
        self.next_page = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        self.who = QtWidgets.QLabel(objectName="muted", wordWrap=True)
        head.addWidget(self.who, 1)
        self.login_button = QtWidgets.QToolButton(text="Log in…", toolTip="Server address and access key")
        self.login_button.clicked.connect(self.edit_login)
        head.addWidget(self.login_button)
        lay.addLayout(head)
        row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit(placeholderText="PK, UUID or search your calculations")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.refresh)
        row.addWidget(self.search, 1)
        refresh = QtWidgets.QToolButton(text="Refresh", toolTip="Load the list again")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        lay.addLayout(row)
        self.tree = QtWidgets.QTreeWidget(headerHidden=True, objectName="simagentTree")
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self.open(item))
        self.tree.currentItemChanged.connect(self._selected)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self.more = QtWidgets.QPushButton("Older calculations", objectName="pluginButton")
        self.more.clicked.connect(lambda: self.refresh(more=True))
        self.more.setVisible(False)
        lay.addWidget(self.more)
        self.path = QtWidgets.QLabel(objectName="muted", wordWrap=True)
        self.path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.path)
        buttons = QtWidgets.QHBoxLayout()
        self.buttons = {}
        for text, slot, tip in (("Open", self.open, "Open it (double click does the same)"),
                                ("Insert cell", self.insert_cell, "A notebook cell pipe = load(...), not run"),
                                ("Copy path", self.copy_path, "The simagent: path, for load() or Open location")):
            b = QtWidgets.QPushButton(text, toolTip=tip)
            b.clicked.connect(lambda _=False, slot=slot: slot())
            b.setEnabled(False)
            buttons.addWidget(b)
            self.buttons[text] = b
        lay.addLayout(buttons)
        self.note = QtWidgets.QLabel(objectName="muted", wordWrap=True)
        self.note.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.note)
        context.on("settings", lambda _s: self.refresh())
        self.refresh()

    # ------------------------------------------------------------ requests
    def _run(self, fn, then):
        job = _Job(fn, self)
        job.done.connect(then)
        job.finished.connect(lambda: self.jobs.remove(job) if job in self.jobs else None)
        self.jobs.append(job)
        job.start()
        return job

    def wait(self, ms: int = 10000):
        """Block until every request has answered (tests, screenshots)."""
        while self.jobs:
            self.jobs[0].wait(ms)
            QtWidgets.QApplication.processEvents()

    def connection(self):
        try:
            return client.get_connection()
        except client.SimAgentError:
            return None

    # ------------------------------------------------------------ list
    def refresh(self, more: bool = False):
        conn = self.connection()
        if conn is None:
            self.who.setText("Not logged in to sim-agent.")
            self.login_button.setText("Log in…")
            self.tree.clear()
            self.more.setVisible(False)
            self._say("Log in with the server address and an access key to list your calculations.")
            return
        self.who.setText(f"{conn.user} at {conn.url}")
        self.login_button.setText("Settings…")
        text = self.search.text().strip()
        if not more:
            self.tree.clear()
            self.next_page = None
        api = client.Client(conn)
        if text and _NODE_TEXT.match(text) and not more:
            self._say("Looking up…")
            self._run(lambda: api.node(text), lambda node, err: self._show_nodes([node] if node else [], err, None))
        else:
            before = self.next_page if more else None
            self._say("Loading your calculations…")
            self._run(lambda: api.nodes(limit=50, before=before, q=text or None),
                      lambda page, err: self._show_nodes((page or {}).get("nodes", []), err,
                                                         (page or {}).get("next")))

    def _show_nodes(self, nodes, error, next_page):
        if error:
            self._say(error, error=True)
            return
        conn = self.connection()
        for n in nodes:
            item = QtWidgets.QTreeWidgetItem([_row_text(n)])
            item.setToolTip(0, "\n".join(x for x in (n.get("label"), n.get("description"), n.get("ctime")) if x))
            item.setData(0, PATH, stable(conn.name, n["uuid"]) if conn else f"simagent:{n['pk']}")
            if n.get("kind", "process") == "process" and n.get("calculations") is None:
                item.setData(0, NODE, n["pk"])
                item.addChild(QtWidgets.QTreeWidgetItem(["…"]))     # filled when expanded
            elif n.get("calculations") is not None:
                self._fill(item, n)
            self.tree.addTopLevelItem(item)
        self.next_page = next_page
        self.more.setVisible(bool(next_page))
        self._say("" if self.tree.topLevelItemCount() else "No calculations found.")

    # ------------------------------------------------------------ one calculation
    def _expand(self, item):
        pk = item.data(0, NODE)
        if pk is None:
            return
        item.setData(0, NODE, None)
        conn = self.connection()
        if conn is None:
            return
        api = client.Client(conn)
        self._run(lambda: api.node(pk), lambda node, err: self._filled(item, node, err))

    def _filled(self, item, node, error):
        item.takeChildren()
        if error:
            item.addChild(QtWidgets.QTreeWidgetItem([error]))
            return
        self._fill(item, node)
        item.setExpanded(True)

    def _fill(self, item, node):
        """Steps (restarts of one calculation together), their files, and output structures."""
        item.takeChildren()
        head = item.data(0, PATH)
        runs: OrderedDict = OrderedDict()
        for calc in node.get("calculations", []):
            runs.setdefault(run_key(calc), []).append(calc)
        for key, calcs in runs.items():
            step = f"{head}/{'/'.join(key)}" if key else head
            label = "/".join(key) or "(this calculation)"
            if len(calcs) > 1:
                label += f"  ({len(calcs)} runs)"
            last = calcs[-1]
            child = QtWidgets.QTreeWidgetItem([f"{label}  {STATE.get(last.get('process_state'), '')}".rstrip()])
            child.setToolTip(0, (last.get("remote") or {}).get("path") or "no work directory on record")
            if any(TRAJECTORY_FILE in c.get("files", []) for c in calcs):
                child.setData(0, PATH, step)
            for name in last.get("files", []):
                f = QtWidgets.QTreeWidgetItem([name])
                f.setData(0, PATH, f"{step}/{name}")
                child.addChild(f)
            if last.get("remote"):
                for name in CLUSTER_FILES:
                    if name not in last.get("files", []):
                        f = QtWidgets.QTreeWidgetItem([f"{name}  (on the cluster, over SSH)"])
                        f.setData(0, PATH, f"{step}/{name}")
                        child.addChild(f)
            item.addChild(child)
        for s in node.get("structures", []):
            child = QtWidgets.QTreeWidgetItem([f"{s['link']}  {s.get('formula') or ''}".rstrip()])
            child.setData(0, PATH, f"{head}/{s['link']}")
            item.addChild(child)

    # ------------------------------------------------------------ actions
    def _selected(self, item, _previous=None):
        path = item.data(0, PATH) if item is not None else None
        self.path.setText(path or "")
        for b in self.buttons.values():
            b.setEnabled(bool(path))

    def _current_path(self, item=None):
        item = item or self.tree.currentItem()
        return item.data(0, PATH) if item is not None else None

    def open(self, item=None):
        path = self._current_path(item)
        if not path:
            return None
        window = self.context.window
        if hasattr(window, "open_location_dialog"):
            return window.open_location_dialog(path)
        return window.open_file(path)

    def insert_cell(self):
        path = self._current_path()
        if path:
            self.context.insert_cell(f"pipe = load({path!r})")

    def copy_path(self):
        path = self._current_path()
        if path:
            QtWidgets.QApplication.clipboard().setText(path)
            self.context.message(f"copied {path}")

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None or not item.data(0, PATH):
            return
        self.tree.setCurrentItem(item)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Open", lambda: self.open(item))
        menu.addAction("Insert load() cell", self.insert_cell)
        menu.addAction("Copy path", self.copy_path)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def edit_login(self):
        window = self.context.window
        if hasattr(window, "edit_plugin_settings"):
            window.edit_plugin_settings("simagent")
        else:
            self.context.open_preferences("sim-agent")
        self.refresh()

    def _say(self, text: str, error: bool = False):
        self.note.setText(text)
        self.note.setObjectName("cellError" if error else "muted")
        self.note.style().polish(self.note)
        self.note.setVisible(bool(text))

