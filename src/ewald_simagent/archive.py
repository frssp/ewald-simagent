"""The step tree of a finished sim-agent workflow, read from its AiiDA archive by ewald-aiida.

sim-agent serves a finished workflow as an AiiDA archive (``GET /api/v1/nodes/{node}/archive.aiida``,
``content=metadata``: the database without the file contents, kilobytes). ewald-aiida opens it
and describes the workflow as nested steps (restarts joined) that say what each calculation was
run with: ``uuid``, ``inputs`` (INCAR, k-points, structure and its extras, ``parents``),
``shape`` (``kind`` static/relax/bands/..., ``flags`` slab, potential, ...), ``files`` and
``extras``. That tree is the workflow's tree here too (one builder for AiiDA and sim-agent,
EWALD decision D99); :func:`tree` only gives its steps ``simagent:`` paths, the server's PKs
and file outputs, since files still come through the results API and SSH.

AiiDA exports only sealed processes, so a running workflow has no archive: :mod:`.source` then
lists its calculations one by one, each opened on its own.
"""
from __future__ import annotations

from ewald.plugin_api import WorkflowOutput, WorkflowStep

from .client import Client
from .reader import cache_dir
from .resolve import TRAJECTORY_FILE

#: process states of a sealed process, the only ones AiiDA exports
SEALED = ("finished", "excepted", "killed")
ARCHIVE = ".archive-metadata.aiida"
#: files read from the work directory on the cluster when asked for (not retrieved by AiiDA)
CLUSTER_FILES = ("CHGCAR", "LOCPOT", "ELFCAR")

_opened: dict = {}


def _open_archive(path):
    """ewald-aiida's Workflow of the archive at ``path`` (replaced in tests)."""
    import ewald_aiida

    return ewald_aiida.open_archive(path)


def download(conn, pk, uuid: str):
    """The local path of node ``pk``'s metadata archive, downloaded once (a sealed workflow's
    never changes) into its cache folder; raises :class:`.client.SimAgentError` (409 while
    something below still runs, 404 on a server without the route)."""
    dest = cache_dir() / uuid / ARCHIVE
    if not dest.exists():
        Client(conn).download(f"nodes/{pk}/archive.aiida?content=metadata", dest)
    return dest


def outputs(path: str, files, work_dir: str) -> list:
    """The files of a calculation at ``path``: those AiiDA retrieved, then the big ones left in
    its work directory, read over SSH."""
    out = [WorkflowOutput(name, f"{path}/{name}") for name in files]
    if work_dir:
        out += [WorkflowOutput(name, f"{path}/{name}", kind="volumetric", note="on the cluster, over SSH")
                for name in CLUSTER_FILES if name not in files]
    return out


def _copy(theirs, path: str, pk_of: dict) -> WorkflowStep:
    files = list(getattr(theirs, "files", None) or [])
    step = WorkflowStep(
        theirs.name, path=path if TRAJECTORY_FILE in files else "", state=theirs.state,
        exit_status=theirs.exit_status, runs=theirs.runs, id=pk_of.get(getattr(theirs, "uuid", ""), ""),
        work_dir=theirs.work_dir, scalars=dict(theirs.scalars), outputs=outputs(path, files, theirs.work_dir))
    # set, not passed: an EWALD from before these were WorkflowStep fields keeps them as attributes
    step.uuid = getattr(theirs, "uuid", "") or ""
    step.inputs = dict(getattr(theirs, "inputs", None) or {})
    step.shape = dict(getattr(theirs, "shape", None) or {})
    step.files = files
    step.extras = dict(getattr(theirs, "extras", None) or {})
    step.steps = [_copy(s, f"{path}/{s.name}", pk_of) for s in theirs.steps]
    return step


def tree(conn, desc: dict, head: str) -> list:
    """The steps of finished workflow ``desc`` (a node description) from its archive, with
    ``simagent:`` paths under ``head`` (the call labels, as the reader resolves them) and the
    server's PKs; raises SimAgentError, ImportError (no ewald-aiida) or what a bad archive does."""
    path = download(conn, desc["pk"], desc["uuid"])
    theirs = _opened.get(str(path))
    if theirs is None:
        theirs = _opened[str(path)] = _open_archive(path)
    pk_of = {c["uuid"]: str(c["pk"]) for c in desc.get("calculations", []) if c.get("uuid")}
    return [_copy(s, f"{head}/{s.name}", pk_of) for s in theirs.steps]
