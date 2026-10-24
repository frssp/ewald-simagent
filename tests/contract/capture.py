"""Capture sim-agent's ``/api/v1`` answers into ``tests/contract/sim-agent-v1.json``.

Run it with the Python of a sim-agent checkout (frssp/sim_agent installed with ``.[dev,web]``,
which brings aiida-core and fastapi)::

    /path/to/sim_agent/.venv/bin/python tests/contract/capture.py [--check]

It builds a small calculation history in a throw-away SQLite AiiDA profile (no PostgreSQL,
RabbitMQ or daemon), serves it through sim-agent's own routes (``register_results_endpoints``
on a FastAPI test client) and records every request the plugin makes with the answer. Nothing
here re-implements sim-agent: the bodies are what its serializers produce, so
``tests/test_contract.py`` can hold the plugin and the fake backend in ``conftest.py`` to them.

The history mirrors what sim-agent submits: a WorkGraph whose steps are VASP work chains
calling VaspCalculations (``relax/iteration_01``), with retrieved files, a work directory on
a cluster, output structures and ``misc`` numbers, tagged with the chat that submitted it.

Volatile values (UUIDs, times, PKs, the token, cursors, the version) are replaced by stable
placeholders, so a capture from the same sim-agent is byte-identical and a diff shows only
what changed in the contract. ``--check`` compares instead of writing (exit 1 on a change).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import ClassVar

GOLDEN = Path(__file__).with_name("sim-agent-v1.json")
USER, PASSWORD = "alice", "s3cret"
VASPRUN = b"<?xml version='1.0'?><modeling><generator/></modeling>\n"


def _profile(root: Path):
    from aiida import load_profile
    from aiida.manage.configuration import create_profile, get_config

    os.environ["AIIDA_PATH"] = str(root / "aiida")
    config = get_config(create=True)
    profile = create_profile(config, storage_backend="core.sqlite_dos",
                             storage_config={"filepath": str(root / "storage")}, broker_backend=None,
                             broker_config=None, name=f"contract-{secrets.token_hex(4)}",
                             email="test@localhost", is_test_profile=True)
    config.set_default_profile(profile.name)
    config.store()
    load_profile(profile.name, allow_switch=True)


def _computer():
    from aiida import orm

    computer = orm.Computer(label="supercom", hostname="hpc.example.org", transport_type="core.ssh",
                            scheduler_type="core.direct", workdir="/scratch/{username}").store()
    computer.configure(username=USER, look_for_keys=False, key_policy="AutoAddPolicy")
    return computer


def _structure(formula_atoms, a=5.43):
    from aiida import orm

    s = orm.StructureData(cell=[[a, 0, 0], [0, a, 0], [0, 0, a]])
    for i, symbol in enumerate(formula_atoms):
        s.append_atom(position=(i * a / 4, i * a / 4, i * a / 4), symbols=symbol)
    return s


def _process(cls, process_label, state="finished", exit_status=0, **kw):
    node = cls(**kw)
    node.set_process_label(process_label)
    node.set_process_state(state)
    if exit_status is not None:
        node.set_exit_status(exit_status)
    return node


def _calc(computer, caller, link, files, structure, n, state="finished", remote=True):
    """A VaspCalculation called by ``caller`` as ``link``, with a retrieved folder."""
    from aiida import orm
    from aiida.common.links import LinkType

    calc = _process(orm.CalcJobNode, "VaspCalculation", state, 0 if state == "finished" else None,
                    computer=computer, process_type="aiida.calculations:vasp.vasp")
    calc.set_option("resources", {"num_machines": 1})
    calc.base.links.add_incoming(structure, LinkType.INPUT_CALC, "structure")
    calc.base.links.add_incoming(caller, LinkType.CALL_CALC, link)
    calc.store()
    if state == "finished":
        retrieved = orm.FolderData()
        for name in files:
            retrieved.base.repository.put_object_from_filelike(
                io.BytesIO(VASPRUN if name == "vasprun.xml" else b"{}"), name)
        retrieved.base.links.add_incoming(calc, LinkType.CREATE, "retrieved")
        retrieved.store()
    if remote:
        folder = orm.RemoteData(computer=computer, remote_path=f"/scratch/{USER}/calc{n:02d}")
        folder.base.links.add_incoming(calc, LinkType.CREATE, "remote_folder")
        folder.store()
    return calc


def _history(computer) -> dict:
    """Alice's submissions, oldest first, and one of bob's; the PKs by name."""
    from aiida import orm
    from aiida.common.links import LinkType
    from sim_agent.tagging import EXTRAS_KEY, get_or_create_user_group

    pk = {}
    si = _structure(["Si", "Si"]).store()

    # a WorkGraph<vasp_bands>: relax (restarted once) -> scf -> bands, from the chat "t-bands"
    wg = _process(orm.WorkflowNode, "WorkGraph<vasp_bands>", label="Si bands")
    wg.description = "band structure of Si"
    wg.base.links.add_incoming(si, LinkType.INPUT_WORK, "structure")
    wg.store()
    n = 0
    for step, iterations, files in (("relax", 2, ["OUTCAR", "vasprun.xml"]),
                                    ("scf", 1, ["OUTCAR", "vasprun.xml"]),
                                    ("bands", 1, ["OUTCAR", "locpot_planar_avg.json", "vasprun.xml"])):
        chain = _process(orm.WorkChainNode, "VaspWorkChain")
        chain.base.links.add_incoming(si, LinkType.INPUT_WORK, "structure")
        chain.base.links.add_incoming(wg, LinkType.CALL_WORK, step)
        chain.store()
        for i in range(1, iterations + 1):
            n += 1
            calc = _calc(computer, chain, f"iteration_{i:02d}", files, si, n)
            pk[f"{step}_{i}"] = calc.pk
        chain.seal()
    relaxed = _structure(["Si", "Si"], 5.47)
    relaxed.base.links.add_incoming(calc, LinkType.CREATE, "structure")
    relaxed.store()
    relaxed.base.links.add_incoming(wg, LinkType.RETURN, "relaxed_structure")
    misc = orm.Dict({"band_gap": 0.61, "total_energy": -10.8, "converged": True, "note": "x"})
    misc.base.links.add_incoming(calc, LinkType.CREATE, "misc")
    misc.store()
    misc.base.links.add_incoming(wg, LinkType.RETURN, "misc")
    wg.seal()
    wg.base.extras.set("sim_agent_template", "vasp_bands")
    wg.base.extras.set(EXTRAS_KEY, {"session_id": "t-bands", "user": USER})
    pk.update(bands=wg.pk, si=si.pk, relaxed=relaxed.pk)
    uuid = wg.uuid

    # a work function graph from a chat that was deleted since
    fe = _structure(["Fe"], 2.87).store()
    wf = _process(orm.WorkflowNode, "WorkGraph<vasp_workfunction>")
    wf.base.links.add_incoming(fe, LinkType.INPUT_WORK, "structure")
    wf.store()
    n += 1
    _calc(computer, wf, "slab_111_t1_static", ["OUTCAR", "locpot_planar_avg.json", "vasprun.xml"], fe, n)
    wf.seal()
    wf.base.extras.set("sim_agent_template", "vasp_workfunction")
    wf.base.extras.set(EXTRAS_KEY, {"session_id": "t-gone", "user": USER})
    pk["workfunction"] = wf.pk

    # a calculation still running, submitted outside a chat
    n += 1
    run = _process(orm.CalcJobNode, "VaspCalculation", "running", None, computer=computer,
                   process_type="aiida.calculations:vasp.vasp")
    run.set_option("resources", {"num_machines": 1})
    run.base.links.add_incoming(si, LinkType.INPUT_CALC, "structure")
    run.store()
    folder = orm.RemoteData(computer=computer, remote_path=f"/scratch/{USER}/calc{n:02d}")
    folder.base.links.add_incoming(run, LinkType.CREATE, "remote_folder")
    folder.store()
    pk["running"] = run.pk

    # a process with no input structure (the listing has no formula for it)
    bare = _process(orm.WorkflowNode, "WorkGraph<vasp_convergence>").store()
    bare.seal()

    group = get_or_create_user_group(USER)
    group.add_nodes([wg, wf, run, bare])

    other = _process(orm.WorkflowNode, "WorkGraph<vasp_relax>").store()
    other.seal()
    get_or_create_user_group("bob").add_nodes([other])
    pk["bob"] = other.pk
    return {**pk, "bands_uuid": uuid}


class _Store:
    """The session store: the chat names sim-agent shows (a renamed chat; a deleted one)."""

    records: ClassVar[dict] = {"t-bands": {"user_id": USER, "name": "", "first_query": "Si band structure"}}

    def get(self, thread_id):
        return self.records.get(thread_id)


def _exchanges(pk: dict) -> list[dict]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sim_agent import persistence
    from sim_agent.web import results_api

    persistence.get_session_store = lambda: _Store()
    app = FastAPI()
    results_api.register_results_endpoints(app)
    http = TestClient(app)
    out: list[dict] = []

    def ask(name, method, path, auth=None, body=None):
        headers = {"Authorization": auth} if auth else {}
        resp = http.request(method, "/api/v1/" + path, headers=headers, json=body)
        ctype = resp.headers.get("content-type", "").split(";")[0]
        try:
            payload = resp.json() if "json" in ctype else None
        except ValueError:
            payload = None
        out.append({"name": name, "method": method, "path": path, "status": resp.status_code,
                    "content_type": ctype, "json": payload,
                    **({"text": resp.text} if payload is None and ctype.startswith(("text", "chemical"))
                       else {})})
        return payload

    basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    wrong = "Basic " + base64.b64encode(f"{USER}:nope".encode()).decode()
    ask("token, wrong password", "POST", "tokens", wrong, {"name": "ewald on pc", "ttl_days": 180})
    token = ask("token", "POST", "tokens", basic, {"name": "ewald on pc", "ttl_days": 180})["token"]
    bearer = f"Bearer {token}"
    ask("whoami, no key", "GET", "whoami")
    ask("whoami, revoked key", "GET", "whoami", "Bearer sa_pat_revoked")
    ask("whoami", "GET", "whoami", bearer)
    ask("list", "GET", "nodes?limit=50", bearer)
    first = ask("list, one row", "GET", "nodes?limit=1", bearer)
    ask("list, next page", "GET", f"nodes?limit=1&cursor={first['next_cursor']}", bearer)
    ask("list, search by chat name", "GET", "nodes?limit=50&q=band", bearer)
    ask("list, bad limit", "GET", "nodes?limit=0", bearer)
    ask("list, bad cursor", "GET", "nodes?limit=50&cursor=nonsense", bearer)
    ask("workgraph", "GET", f"nodes/{pk['bands']}", bearer)
    ask("workgraph by uuid prefix", "GET", f"nodes/{pk['bands_uuid'][:8]}", bearer)
    ask("workgraph from a deleted chat", "GET", f"nodes/{pk['workfunction']}", bearer)
    ask("running calculation", "GET", f"nodes/{pk['running']}", bearer)
    ask("calculation under a workgraph", "GET", f"nodes/{pk['relax_2']}", bearer)
    ask("structure", "GET", f"nodes/{pk['relaxed']}", bearer)
    ask("structure as extxyz", "GET", f"nodes/{pk['relaxed']}/structure.extxyz", bearer)
    ask("retrieved file", "GET", f"nodes/{pk['relax_2']}/files/vasprun.xml", bearer)
    ask("file not retrieved", "GET", f"nodes/{pk['relax_2']}/files/CHGCAR", bearer)
    ask("another user's node", "GET", f"nodes/{pk['bob']}", bearer)
    ask("no such node", "GET", "nodes/999999", bearer)
    ask("short uuid prefix", "GET", "nodes/abc", bearer)
    return out


def _stable(exchanges: list[dict], pk: dict) -> str:
    """The exchanges as JSON with volatile values replaced by placeholders."""
    from aiida import orm
    from sim_agent.web import results_api

    text = json.dumps(exchanges, indent=1, sort_keys=False)
    # UUIDs in order of appearance, kept distinct; a prefix of one stays a prefix
    uuids: dict = {}
    for u in re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text):
        uuids.setdefault(u, None)
    uuids[pk.pop("bands_uuid")] = None    # asked for by prefix before any answer named it
    uuids = {u: f"cafe{i:04x}-0000-4000-8000-000000000000" for i, u in enumerate(uuids, 1)}
    for real, fake in uuids.items():
        text = text.replace(real, fake).replace(f"nodes/{real[:8]}", f"nodes/{fake[:8]}")
    # PKs: 1000 + rank, in creation order, wherever a PK appears as a value or in a path
    ranks = {v: 1000 + i for i, v in enumerate(sorted(set(pk.values())))}
    for node in orm.QueryBuilder().append(orm.Node, project="id").all(flat=True):
        ranks.setdefault(node, 1000 + len(ranks))
    text = re.sub(r"(\"pk\": |nodes/|node ')(\d+)(?=[,}\n/\"?'])",
                  lambda m: m.group(1) + str(ranks.get(int(m.group(2)), m.group(2))), text)
    text = re.sub(r'"(ctime|created|expires)": "[^"]+"', r'"\1": "2026-10-24T00:00:00+00:00"', text)
    text = re.sub(r'sa_pat_[A-Za-z0-9_-]{20,}', "sa_pat_TOKEN", text)
    text = re.sub(r'"(id|token_id)": "[0-9a-f]{8}"', r'"\1": "1a2b3c4d"', text)
    cursors = dict.fromkeys(m for m in re.findall(r'"next_cursor": "([^"]+)"', text))
    for i, c in enumerate(cursors, 1):
        text = text.replace(c, f"CURSOR{i}")
    version = __import__("sim_agent").__version__
    text = text.replace(f'"version": "{version}"', '"version": "VERSION"')
    commit = subprocess.run(["git", "-C", str(Path(results_api.__file__).parent), "rev-parse", "HEAD"],
                            check=False, capture_output=True, text=True).stdout.strip() or "unknown"
    return json.dumps({"schema": results_api.SCHEMA,
                       "captured_from": f"frssp/sim_agent {commit}",
                       "exchanges": json.loads(text)}, indent=1) + "\n"


def _quiet() -> None:
    """sim-agent logs every request; keep warnings only, so ``--check`` prints just the verdict."""
    import logging

    import structlog

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))


def capture() -> str:
    with tempfile.TemporaryDirectory(prefix="simagent-contract-") as tmp:
        root = Path(tmp)
        os.environ.update(SIM_AGENT_USER=USER, SIM_AGENT_PASSWORD=PASSWORD,
                          SIM_AGENT_PASSWORD_STORE=str(root / "passwords"), TMPDIR=str(root / "tmp"))
        (root / "tmp").mkdir()
        tempfile.tempdir = None
        _profile(root)
        _quiet()
        pk = _history(_computer())
        return _stable(_exchanges(pk), pk)


def _without_commit(text: str) -> dict:
    data = json.loads(text)
    data.pop("captured_from", None)
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare with the committed capture")
    parser.add_argument("-o", "--output", type=Path, default=GOLDEN)
    args = parser.parse_args(argv)
    text = capture()
    if args.check:
        if _without_commit(text) == _without_commit(args.output.read_text(encoding="utf-8")):
            print(f"{args.output.name} matches sim-agent")
            return 0
        import difflib

        old = json.dumps(_without_commit(args.output.read_text(encoding="utf-8")), indent=1).splitlines()
        new = json.dumps(_without_commit(text), indent=1).splitlines()
        sys.stdout.writelines(line + "\n" for line in difflib.unified_diff(
            old, new, args.output.name, "sim-agent now", lineterm="", n=2))
        print(f"\nsim-agent's answers differ from {args.output}; rerun without --check to update it",
              file=sys.stderr)
        return 1
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
