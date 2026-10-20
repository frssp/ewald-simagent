"""Walk sim-agent's node summaries into the description :mod:`ewald_simagent.resolve` reads.

sim-agent's node API (``sim-agent.nodes/1``) answers one node at a time: a workflow lists the
processes it ``called``, a CalcJob names its ``retrieved`` folder and its ``remote`` work
directory, and the folder lists its ``files``. This module follows those links from the node
the user named down to every CalcJob, in call order, and gathers what a ``simagent:`` path can
pick: the steps (call link labels), the retrieved files, the work directories, the output
structures and the numbers in the output ``Dict``s.
"""
from __future__ import annotations

from .client import Client

#: deeper than any sim-agent workflow (WorkGraph -> WorkChain -> CalcJob), so a cycle stops
MAX_DEPTH = 8


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _step_label(child: dict) -> str:
    return child.get("link_label") or child.get("label") or child.get("process_label") or str(child["pk"])


def _calcjobs(client: Client, summary: dict, path: tuple, depth: int) -> list[dict]:
    proc = summary.get("process", {})
    if "retrieved_pk" in proc or "remote" in proc:
        files = []
        if proc.get("retrieved_pk"):
            files = [f["path"] for f in client.node(proc["retrieved_pk"]).get("files", [])]
        return [{"pk": summary["pk"], "uuid": summary["uuid"], "path": list(path),
                 "process_label": proc.get("process_label"), "process_state": proc.get("process_state"),
                 "exit_status": proc.get("exit_status"), "exit_message": proc.get("exit_message"),
                 "retrieved_pk": proc.get("retrieved_pk"), "files": files, "remote": proc.get("remote")}]
    if depth >= MAX_DEPTH:
        return []
    found = []
    for child in proc.get("called", []):
        found += _calcjobs(client, client.node(child["pk"]), path + (_step_label(child),), depth + 1)
    return found


def collect(client: Client, ident) -> dict:
    """The description of node ``ident`` (PK or full UUID): ``kind`` "process", "structure" or
    "data", and for a process its ``calculations``, ``structures`` and ``scalars``."""
    top = client.node(ident)
    base = {"pk": top["pk"], "uuid": top["uuid"], "node_type": top.get("node_type"),
            "label": top.get("label"), "class": top.get("class")}
    if top.get("kind") != "process":
        structure = top.get("data", {}).get("structure")
        if structure is not None:
            return {**base, "kind": "structure", "formula": structure.get("formula"),
                    "natoms": structure.get("num_sites")}
        return {**base, "kind": "data"}
    proc = top.get("process", {})
    structures, scalars = [], {}
    for link in top.get("outputs", []):
        if link.get("class") == "StructureData":
            structures.append({"link": link["link_label"], "pk": link["pk"], "uuid": link["uuid"]})
        elif link.get("class") == "Dict":
            values = client.node(link["pk"]).get("data", {}).get("dict", {})
            for key, value in values.items():
                if _number(value):
                    scalars.setdefault(key, value)
    return {**base, "kind": "process", "process_label": proc.get("process_label"),
            "process_state": proc.get("process_state"), "exit_status": proc.get("exit_status"),
            "template": proc.get("template"), "scalars": scalars, "structures": structures,
            "calculations": _calcjobs(client, top, (), 0),
            "truncated": bool(top.get("links_truncated"))}
