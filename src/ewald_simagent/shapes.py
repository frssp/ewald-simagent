"""What each step of a sim-agent workflow was run with, read from the workflow's AiiDA archive.

sim-agent serves a finished workflow as an AiiDA archive (``GET /api/v1/nodes/{node}/archive.aiida``,
``content=metadata``: the database without the file contents, kilobytes). ewald-aiida opens it
and describes every calculation by its inputs (``step.inputs``: INCAR, k-points, structure and
its extras, ``parents``) and a ``step.shape`` (``kind`` static/relax/bands/..., ``flags`` slab,
potential, ...). :func:`attach` copies those onto the steps of the workflow this plugin built
from the JSON description, matched by the UUID of the calculation each step was read from, so
that :mod:`.analyses` can find the steps by what they computed and not only by sim-agent's task
names. A workflow still running (AiiDA exports only sealed processes), a server without the
archive route, or an EWALD without ewald-aiida leave the steps as they were: the recipes then
go by the names.
"""
from __future__ import annotations

from .client import Client, SimAgentError, get_connection
from .reader import cache_dir
from .uri import parse

#: process states of a sealed process, the only ones AiiDA exports
SEALED = ("finished", "excepted", "killed")
ARCHIVE = ".archive-metadata.aiida"


def _open_archive(path):
    """ewald-aiida's Workflow of the archive at ``path`` (replaced in tests)."""
    import ewald_aiida

    return ewald_aiida.open_archive(path)


def archive(wf, connection: str | None = None):
    """The local path of ``wf``'s metadata archive, downloaded once (a sealed workflow's never
    changes) into the workflow's own cache folder; None while it runs or when the server has none."""
    if wf.state not in SEALED or not wf.steps:   # a single calculation: its files say enough
        return None
    uri = parse(wf.path)
    conn = get_connection(connection or uri.connection)
    dest = cache_dir() / str(uri.node) / ARCHIVE
    if not dest.exists():
        try:
            Client(conn).download(f"nodes/{wf.id}/archive.aiida?content=metadata", dest)
        except SimAgentError:     # 409 not finished below, 404 an older server: go by the names
            return None
    return dest


def _structure_extras(step, by_calc: dict, depth: int = 4) -> dict:
    """The extras of ``step``'s input structure, completed by those of the structures it came
    from (a static's structure is the relaxation's output, which has none of its own)."""
    own = dict(((step.inputs or {}).get("structure") or {}).get("extras") or {})
    if depth:
        for parent in (step.inputs or {}).get("parents") or []:
            up = by_calc.get(str(parent.get("pk")))
            if up is not None:
                own = {**_structure_extras(up, by_calc, depth - 1), **own}
    return own


def attach(wf, connection: str | None = None) -> str:
    """Give ``wf``'s steps ``shape``, ``inputs``, ``files`` and ``structure_extras`` from its
    archive. Returns "" when it did, else why not (the steps then stay as they were)."""
    if getattr(wf, "shapes_from", None) is not None:
        return wf.shapes_from
    wf.shapes_from = why = _attach(wf, connection)
    return why


def _attach(wf, connection) -> str:
    try:
        path = archive(wf, connection)
    except SimAgentError as exc:
        return str(exc)
    if path is None:
        return "no archive (a single calculation, still running, or a server without one)"
    try:
        theirs = _open_archive(path)
    except ImportError:
        return "ewald-aiida is not installed"
    except Exception as exc:  # noqa: BLE001 - a bad archive must not hide the workflow
        return f"the archive did not open: {exc}"
    steps = [s for _, s in theirs.walk()]
    by_uuid = {s.calc_uuid: s for s in steps if getattr(s, "calc_uuid", "")}
    by_calc = {s.calc_id: s for s in steps if getattr(s, "calc_id", "")}
    matched = 0
    for _, step in wf.walk():
        found = by_uuid.get(getattr(step, "calc_uuid", ""))
        if found is None:
            continue
        step.shape, step.inputs = dict(found.shape), dict(found.inputs)
        step.files = list(found.files)
        step.structure_extras = _structure_extras(found, by_calc)
        matched += 1
    return "" if matched else "no step of the archive matches this workflow's calculations"
