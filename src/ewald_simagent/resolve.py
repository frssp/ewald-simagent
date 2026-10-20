"""Which files a ``simagent:`` path means, from sim-agent's description of the node.

sim-agent describes a process as the VASP calculations under it, in call order, each with
its call path (``["relax", "iteration_01"]``), the files AiiDA retrieved and the work
directory left on the cluster. A path picks from that description in this order:

1. an output structure of the process by link label (``simagent:1234/relaxed_structure``);
2. steps, by any label on the call path (``simagent:1234/bands``), as many as needed;
3. one file of the chosen step (``simagent:1234/scf/CHGCAR``). A retrieved file comes from
   sim-agent; any other file is read from the work directory over SSH.

Without a file, the trajectory of the step is meant: the restarts of one calculation joined in
call order, that is the calculations whose call paths differ only in a last ``iteration_NN``
(a relaxation restarted twice gives one trajectory). With no step either, that is the first
calculation with a vasprun.xml (a workflow's relaxation). When the step holds other, separate
calculations (an MLFF set of structures, a composite graph), only the first is opened and
:func:`resolve` warns with the paths that open the others: their structures differ, so joining
them into one trajectory would be wrong.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

from .client import Connection, SimAgentError

TRAJECTORY_FILE = "vasprun.xml"
_RESTART = re.compile(r"^iteration_\d+$")


class SimAgentWarning(UserWarning):
    """A path opened less than the whole step (one of several separate calculations)."""


def run_key(calc: dict) -> tuple:
    """The call path without a last ``iteration_NN``: restarts of one calculation share it."""
    path = tuple(calc.get("path", ()))
    return path[:-1] if path and _RESTART.match(path[-1]) else path


@dataclass(frozen=True)
class Target:
    """One thing to read: ``kind`` "file" (``name`` of calculation ``calc``, ``where``
    "retrieved" or "remote") or "structure" (the StructureData ``node``)."""

    kind: str
    node: dict
    name: str | None = None
    where: str | None = None


def remote_path(calc: dict, name: str, conn: Connection) -> str:
    """``[user@]host:/workdir/name`` for EWALD's SSH remote files."""
    remote = calc.get("remote")
    if not remote:
        raise SimAgentError(f"Calculation {calc['pk']} has no work directory on record, and "
                            f"{name} was not retrieved")
    host = conn.hosts.get(remote.get("computer"))
    if host is None:
        user = remote.get("username")
        host = f"{user}@{remote['hostname']}" if user else remote["hostname"]
    return f"{host}:{remote['path'].rstrip('/')}/{name}"


def _steps(calcs: list[dict]) -> list[str]:
    seen: list[str] = []
    for c in calcs:
        for label in c["path"]:
            if label not in seen:
                seen.append(label)
    return seen


def _summary(desc: dict) -> str:
    steps = _steps(desc.get("calculations", []))
    links = [s["link"] for s in desc.get("structures", [])]
    return (f"steps: {', '.join(steps) or 'none'}; output structures: {', '.join(links) or 'none'}")


def resolve(desc: dict, parts: tuple = ()) -> list[Target]:
    """The targets ``parts`` name in node description ``desc`` (see the module docstring)."""
    kind = desc.get("kind")
    if kind == "structure":
        if parts:
            raise SimAgentError(f"Node {desc['pk']} is a structure; nothing follows it")
        return [Target("structure", desc)]
    if kind != "process":
        raise SimAgentError(f"Node {desc['pk']} ({desc.get('node_type')}) is neither a calculation "
                            "nor a structure")
    calcs = desc.get("calculations", [])
    structures = {s["link"]: s for s in desc.get("structures", [])}
    rest = list(parts)
    if rest and rest[0] in structures:
        if len(rest) > 1:
            raise SimAgentError(f"{rest[0]} is a structure; nothing follows it")
        return [Target("structure", structures[rest[0]])]

    chosen, stepped = calcs, False
    while rest and any(rest[0] in c["path"] for c in chosen):
        chosen = [c for c in chosen if rest[0] in c["path"]]
        rest.pop(0)
        stepped = True

    if rest:  # a file of the chosen step
        name = "/".join(rest)
        for calc in reversed(chosen):  # the latest run of the step
            if name in calc.get("files", []):
                return [Target("file", calc, name, "retrieved")]
        with_remote = [c for c in chosen if c.get("remote")]
        if not with_remote:
            raise SimAgentError(f"{name} was not retrieved and node {desc['pk']} has no work "
                                f"directory on record ({_summary(desc)})")
        return [Target("file", with_remote[-1], name, "remote")]

    with_traj = [c for c in chosen if TRAJECTORY_FILE in c.get("files", [])]
    if with_traj:
        first = run_key(with_traj[0])
        others = list(dict.fromkeys(run_key(c) for c in with_traj if run_key(c) != first))
        with_traj = [c for c in with_traj if run_key(c) == first]
        if not stepped:   # a workflow's other steps (scf, bands) are no surprise; its own parts are
            others = [k for k in others if k[:1] == first[:1]]
        if others:
            head = "/".join([str(desc["pk"]), *parts])
            shown = ", ".join("simagent:" + "/".join([str(desc["pk"]), *k]) for k in others[:5])
            warnings.warn(f"simagent:{head} holds {len(others) + 1} separate calculations; opened the first "
                          f"({'/'.join(first) or desc['pk']}). Others: {shown}"
                          + (f" and {len(others) - 5} more" if len(others) > 5 else ""), SimAgentWarning,
                          stacklevel=2)
        return [Target("file", c, TRAJECTORY_FILE, "retrieved") for c in with_traj]
    if structures and not stepped:
        return [Target("structure", list(structures.values())[-1])]
    raise SimAgentError(f"Nothing to show for {'/'.join([str(desc['pk']), *parts])}: no "
                        f"{TRAJECTORY_FILE} was retrieved there ({_summary(desc)}). Name a file, "
                        "e.g. /OUTCAR")
