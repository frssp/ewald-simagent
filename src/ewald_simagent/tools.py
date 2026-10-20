"""AI tool for EWALD's Ask AI and MCP server: what a sim-agent node holds, by PK.

Read-only like the rest of the plugin. To open a part, the model loads the ``simagent:`` path it
returns (``load_structure``, or a notebook cell ``pipe = load("simagent:1234/bands")``)."""
from __future__ import annotations


def simagent_node(toolset, node: str, connection: str | None = None) -> dict:
    """What one of the user's sim-agent calculations holds (by AiiDA PK or UUID), with the
    ``simagent:`` path that opens each part: its steps, retrieved files, work directory and output
    structures. Open a part by loading that path (load_structure, or a notebook cell
    ``pipe = load("simagent:1234/bands")``); files that were not retrieved, like CHGCAR, open over SSH."""
    from . import describe
    from .client import Client, get_connection
    from .uri import parse, stable

    uri = parse(node if str(node).startswith("simagent:") else f"simagent:{node}")
    conn = get_connection(connection or uri.connection)
    desc = Client(conn).node(uri.node)
    head = stable(conn.name, desc["uuid"])   # the same calculation on any machine, unlike a PK
    out = {"listing": describe(node, connection), "open": head, "pk": desc["pk"], "kind": desc.get("kind"),
           "process_label": desc.get("process_label"), "process_state": desc.get("process_state"),
           "exit_status": desc.get("exit_status"), "template": desc.get("template"),
           "scalars": desc.get("scalars", {})}
    if desc.get("kind") == "process":
        out["steps"] = [{"path": f"{head}/{'/'.join(c['path'])}" if c.get("path") else head,
                         "process_state": c.get("process_state"), "exit_status": c.get("exit_status"),
                         "retrieved": c.get("files", []), "work_dir": (c.get("remote") or {}).get("path")}
                        for c in desc.get("calculations", [])]
        out["structures"] = [{"path": f"{head}/{s['link']}", "formula": s.get("formula")}
                             for s in desc.get("structures", [])]
    return out


simagent_node.tool_description = " ".join(simagent_node.__doc__.split())
simagent_node.tool_schema = {
    "type": "object",
    "properties": {"node": {"type": "string", "description": "AiiDA PK, UUID (or its beginning) or simagent: path"},
                   "connection": {"type": "string", "description": "saved connection name; omit for the default"}},
    "required": ["node"]}
