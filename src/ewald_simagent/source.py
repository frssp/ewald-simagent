"""sim-agent as an EWALD workflow source (:mod:`ewald.workflow`): your calculations newest first,
each described as steps (restarts joined, call paths nested) with their files and structures.

EWALD builds the side bar, "open as one notebook" and the AI tools ``list_workflows`` /
``describe_workflow`` from this; every path is a ``simagent:`` path the reader opens.
"""
from __future__ import annotations

from collections import OrderedDict

from ewald.plugin_api import Workflow, WorkflowOutput, WorkflowPage, WorkflowStep

from . import client
from .resolve import TRAJECTORY_FILE, run_key
from .uri import parse, stable

#: files read from the work directory on the cluster when asked for (not retrieved by AiiDA)
CLUSTER_FILES = ("CHGCAR", "LOCPOT", "ELFCAR")
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
        """Notebook analyses for a described workflow, by its template (:mod:`.analyses`)."""
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
    """A listing row (``GET /api/v1/nodes``) as a Workflow not described yet."""
    return Workflow(_name(n), id=str(n["pk"]), path=stable(conn.name, n["uuid"]), kind=_name(n),
                    formula=n.get("formula") or "", state=n.get("process_state") or "",
                    exit_status=n.get("exit_status"), label=n.get("label") or "",
                    description=n.get("description") or "", ctime=n.get("ctime") or "")


def _scalars(desc: dict) -> dict:
    """``{"misc": {"energy": ...}}`` flat: ``energy``, or ``misc.energy`` with several groups."""
    groups = desc.get("scalars") or {}
    many = len(groups) > 1
    return {(f"{g}.{k}" if many else k): v for g, values in groups.items() if isinstance(values, dict)
            for k, v in values.items()}


def workflow(desc: dict, conn) -> Workflow:
    """A node description (``GET /api/v1/nodes/{node}``) as a Workflow with its steps."""
    head = stable(conn.name, desc["uuid"])
    wf = row(desc, conn)
    wf.complete = True
    wf.scalars = _scalars(desc)
    if desc.get("kind") != "process":       # a structure or other data node: itself, nothing below
        wf.outputs = [WorkflowOutput(desc.get("node_type") or "node", head, kind="structure",
                                     note=desc.get("formula") or "")]
        return wf
    runs: OrderedDict = OrderedDict()
    for calc in desc.get("calculations", []):
        runs.setdefault(run_key(calc), []).append(calc)
    for key, calcs in runs.items():
        last = calcs[-1]
        path = f"{head}/{'/'.join(key)}" if key else head
        has_frames = any(TRAJECTORY_FILE in c.get("files", []) for c in calcs)
        outputs = [WorkflowOutput(name, f"{path}/{name}") for name in last.get("files", [])]
        if last.get("remote"):
            outputs += [WorkflowOutput(name, f"{path}/{name}", kind="volumetric", note="on the cluster, over SSH")
                        for name in CLUSTER_FILES if name not in last.get("files", [])]
        if not key:                          # the node is a calculation itself
            wf.runs, wf.outputs = len(calcs), outputs
            wf.work_dir = (last.get("remote") or {}).get("path") or ""
            continue
        parent = wf
        for part in key[:-1]:                # call paths nest: bands/scf is scf under bands
            found = next((s for s in parent.steps if s.name == part), None)
            if found is None:
                found = WorkflowStep(part)
                parent.steps.append(found)
            parent = found
        parent.steps.append(WorkflowStep(
            key[-1], path=path if has_frames else "", state=last.get("process_state") or "",
            exit_status=last.get("exit_status"), runs=len(calcs), id=str(last["pk"]),
            work_dir=(last.get("remote") or {}).get("path") or "", outputs=outputs))
    wf.outputs += [WorkflowOutput(s["link"], f"{head}/{s['link']}", kind="structure", note=s.get("formula") or "")
                   for s in desc.get("structures", [])]
    return wf
