"""EWALD plugin: open your finished sim-agent calculations by AiiDA PK, read-only.

```python
from ewald import load

pipe = load("simagent:1234")                    # the workflow's first trajectory (its relaxation)
pipe = load("simagent:1234/bands")              # another step of the workflow
pipe = load("simagent:1234/relaxed_structure")  # an output structure
pipe = load("simagent:1234/scf/CHGCAR")         # a file left on the cluster, read over SSH
pipe = load("simagent:lab/1234")                # another saved connection (a full UUID works too)
```

sim-agent serves only your own calculations (``/api/v1`` of your backend, your web login);
the VASP files are read with EWALD's own readers. :func:`describe` shows what a node holds.
"""
from .client import Client, Connection, SimAgentError, connections, get_connection
from .collect import collect
from .resolve import resolve
from .uri import SimAgentURI, parse

__all__ = ["Client", "Connection", "SimAgentError", "SimAgentURI", "collect", "connections", "describe",
           "get_connection", "parse", "resolve"]
__version__ = "0.1.0"


def describe(node, connection: str | None = None) -> str:
    """What node ``node`` (PK, UUID or ``simagent:`` path) holds: steps, files and the
    ``simagent:`` path that opens each."""
    uri = parse(node if str(node).startswith("simagent:") else f"simagent:{node}")
    conn = get_connection(connection or uri.connection)
    desc = collect(Client(conn), uri.node)
    head = f"simagent:{conn.name}/{desc['pk']}" if connection or uri.connection else f"simagent:{desc['pk']}"
    if desc["kind"] != "process":
        return f"{desc.get('formula') or desc['node_type']} <{desc['pk']}>  -> load({head!r})"
    lines = [f"{desc.get('process_label')} <{desc['pk']}> [{desc.get('process_state')}, exit "
             f"{desc.get('exit_status')}] {desc.get('template') or ''} {desc.get('label') or ''}".rstrip()]
    for s in desc.get("structures", []):
        lines.append(f"  {s['link']}: StructureData <{s['pk']}>  -> {head}/{s['link']}")
    for c in desc.get("calculations", []):
        step = "/".join(c["path"]) or "(this calculation)"
        where = c["remote"]["path"] if c.get("remote") else "no work directory"
        lines.append(f"  {step}: {c['process_label']} <{c['pk']}> [{c['process_state']}, exit "
                     f"{c['exit_status']}]  {where}")
        if c.get("files"):
            lines.append(f"      retrieved: {', '.join(c['files'])}")
    if desc.get("truncated"):
        lines.append("  (sim-agent listed only the first 500 links)")
    return "\n".join(lines)
