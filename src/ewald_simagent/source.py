"""sim-agent as an EWALD workflow source (:mod:`ewald.workflow`): your calculations newest first,
each described as steps with their files and structures.

A finished workflow's steps come from its AiiDA archive, as ewald-aiida reads it (:mod:`.archive`:
nested, restarts joined, each saying what it computed). A workflow still running has no archive
yet: it lists its calculations one by one (restarts joined), each opened on its own, until it
finishes (EWALD decision D99). So does a finished one whose archive cannot be read.

EWALD builds the side bar, "open as one notebook" and the AI tools ``list_workflows`` /
``describe_workflow`` from this; every path is a ``simagent:`` path the reader opens.
"""
from __future__ import annotations

from collections import OrderedDict

from ewald.plugin_api import Workflow, WorkflowOutput, WorkflowPage, WorkflowStep

from . import archive, client
from .archive import SEALED
from .resolve import TRAJECTORY_FILE, run_key
from .uri import parse, stable

#: pages of the listing one list() call reads while they come back empty (a search reads a
#: bounded number of calculations per page on the server)
EMPTY_PAGES = 10


class SimAgentWorkflows:
    """Your sim-agent calculations, by the saved connection (Settings › sim-agent)."""

    title = "sim-agent"
    schemes = ("simagent",)
    settings_page = "simagent"

    def __init__(self, connection: str | None = None):
        self.connection_name = connection

    def _connection(self):
        return client.get_connection(self.connection_name)

    def account(self) -> str:
        try:
            conn = self._connection()
        except client.SimAgentError:
            return ""
        return f"{conn.user} at {conn.url}"

    def list(self, query: str | None = None, cursor: str | None = None) -> WorkflowPage:
        conn = self._connection()
        api = client.Client(conn)
        page = api.nodes(limit=50, cursor=cursor, q=query)
        for _ in range(EMPTY_PAGES - 1):
            if page.get("nodes") or not page.get("next_cursor"):
                break
            page = api.nodes(limit=50, cursor=page["next_cursor"], q=query)
        return WorkflowPage([row(n, conn) for n in page.get("nodes", [])], next=page.get("next_cursor"))

    def analyses(self, wf: Workflow) -> list:
        """Notebook analyses for a described workflow (:mod:`.analyses`)."""
        from .analyses import analyses

        return analyses(wf)

    def describe(self, ref: str) -> Workflow:
        text = str(ref)
        uri = parse(text if text.startswith("simagent:") else f"simagent:{text}")
        conn = client.get_connection(uri.connection or self.connection_name)
        return workflow(client.Client(conn).node(uri.node), conn)


def _name(n: dict) -> str:
    return n.get("template") or n.get("process_label") or n.get("node_type") or "calculation"


def row(n: dict, conn) -> Workflow:
    """A listing row (``GET /api/v1/nodes``) as a Workflow not described yet. Its group is the
    name of the sim-agent chat that submitted it (``chat``, from a server that sends one)."""
    wf = Workflow(_name(n), id=str(n["pk"]), path=stable(conn.name, n["uuid"]), kind=_name(n),
                  formula=n.get("formula") or "", state=n.get("process_state") or "",
                  exit_status=n.get("exit_status"), label=n.get("label") or "",
                  description=n.get("description") or "", ctime=n.get("ctime") or "")
    # set, not passed: an EWALD without Workflow.group then just ignores it
    wf.group = chat_name(n)
    return wf


def chat_name(n: dict) -> str:
    """The name of the chat that submitted node ``n``: its title, else "chat <id>" for a chat
    that was deleted; "" when the server sends no ``chat``."""
    chat = n.get("chat") or {}
    if not isinstance(chat, dict):
        return ""
    name = str(chat.get("name") or "").strip()
    if name:
        return name
    return f"chat {str(chat['id'])[:8]}" if chat.get("id") else ""


def _scalars(desc: dict) -> dict:
    """``{"misc": {"energy": ...}}`` flat: ``energy``, or ``misc.energy`` with several groups."""
    groups = desc.get("scalars") or {}
    many = len(groups) > 1
    return {(f"{g}.{k}" if many else k): v for g, values in groups.items() if isinstance(values, dict)
            for k, v in values.items()}


def workflow(desc: dict, conn) -> Workflow:
    """A node description (``GET /api/v1/nodes/{node}``) as a Workflow with its steps: from the
    archive when the workflow is finished (:mod:`.archive`), else its calculations one by one."""
    head = stable(conn.name, desc["uuid"])
    wf = row(desc, conn)
    wf.complete = True
    wf.scalars = _scalars(desc)
    if not wf.formula:                       # a description by PK has no formula of its own
        formulas = [s.get("formula") for s in desc.get("structures", []) if s.get("formula")]
        wf.formula = formulas[-1] if formulas else ""
    if desc.get("kind") != "process":       # a structure or other data node: itself, nothing below
        wf.outputs = [WorkflowOutput(desc.get("node_type") or "node", head, kind="structure",
                                     note=desc.get("formula") or "")]
        return wf
    runs: OrderedDict = OrderedDict()
    for calc in desc.get("calculations", []):
        runs.setdefault(run_key(calc), []).append(calc)
    if list(runs) == [()]:                   # the node is a calculation itself
        calcs = runs[()]
        last = calcs[-1]
        wf.work_dir = (last.get("remote") or {}).get("path") or ""
        wf.runs = len(calcs)
        wf.outputs = archive.outputs(head, last.get("files", []), wf.work_dir)
    else:
        wf.steps = _from_archive(desc, conn, head) if wf.state in SEALED and runs else None
        if wf.steps is None:
            wf.steps = [_calculation(key, calcs, head) for key, calcs in runs.items()]
    wf.outputs += [WorkflowOutput(s["link"], f"{head}/{s['link']}", kind="structure", note=s.get("formula") or "")
                   for s in desc.get("structures", [])]
    wf.path = _main_path(wf) or wf.path
    return wf


def _from_archive(desc: dict, conn, head: str):
    """The archive's steps, or None when it cannot be read (an older server, no ewald-aiida,
    a bad archive): the workflow then lists its calculations as a running one does."""
    try:
        return archive.tree(conn, desc, head)
    except Exception:  # noqa: BLE001 - a missing or bad archive must not hide the workflow
        return None


def _calculation(key: tuple, calcs: list, head: str) -> WorkflowStep:
    """One calculation of a workflow (its restarts joined), named by its call path."""
    last = calcs[-1]
    path = f"{head}/{'/'.join(key)}"
    work_dir = (last.get("remote") or {}).get("path") or ""
    has_frames = any(TRAJECTORY_FILE in c.get("files", []) for c in calcs)
    step = WorkflowStep(
        "/".join(key), path=path if has_frames else "", state=last.get("process_state") or "",
        exit_status=last.get("exit_status"), runs=len(calcs), id=str(last["pk"]), work_dir=work_dir,
        outputs=archive.outputs(path, last.get("files", []), work_dir))
    step.uuid = last.get("uuid") or ""       # set, not passed: older EWALDs have no such field
    step.files = list(last.get("files", []))
    return step


def _main_path(wf: Workflow) -> str:
    """The path of the step that the workflow's own path opens (its first step with a
    vasprun.xml, as :func:`.resolve.resolve` picks), so a workflow notebook loads that step once
    under its own name and sets ``pipe = relax``: a ``result`` that is ``relax`` under another
    name made "plot the bands" draw the relaxation's k-mesh. "" when no step has one."""
    return next((s.path for _, s in wf.walk() if s.path and TRAJECTORY_FILE in (s.files or ())), "")
