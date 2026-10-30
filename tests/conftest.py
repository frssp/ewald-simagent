"""A fake sim-agent backend on localhost: the ``/api/v1`` of sim-agent's
``docs/api/results-api-v1.md`` (``sim-agent.node/1``: Bearer on every route, Basic only to
mint a token), answering from :data:`NODES` and serving ``tests/data/vasprun.xml``. Keep it matching
that document."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest

from .band_offset_data import files as band_offset_files
from .workfunction_data import files as workfunction_files

DATA = Path(__file__).parent / "data"
USER, PASSWORD, TOKEN = "alice", "s3cret", "sa_pat_test"


def _calc(pk, path, files, state="finished", remote=True):
    return {"pk": pk, "uuid": f"{pk:08x}-0000-4000-8000-000000000000", "path": path,
            "process_label": "VaspCalculation", "process_state": state, "exit_status": 0,
            "exit_message": None, "files": files, "structures": [],
            "remote": {"computer": "supercom", "hostname": "hpc.example.org", "username": "alice",
                       "path": f"/scratch/alice/{pk}"} if remote else None}


NODES = {
    100: {"kind": "process", "process_label": "WorkGraph<vasp_bands>", "template": "vasp_bands",
          "process_state": "finished", "exit_status": 0, "scalars": {"misc": {"band_gap": 0.61, "total_energy": -10.8}},
          "structures": [{"link": "relaxed_structure", "pk": 200, "uuid": "c8" * 16, "formula": "Si2"}],
          "calculations": [
              _calc(101, ["relax", "iteration_01"], ["OUTCAR", "vasprun.xml"]),
              _calc(102, ["relax", "iteration_02"], ["OUTCAR", "vasprun.xml"]),
              _calc(103, ["scf"], ["OUTCAR", "vasprun.xml"]),
              _calc(104, ["bands"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
          ]},
    300: {"kind": "process", "process_label": "VaspCalculation", "process_state": "running",
          "exit_status": None, "scalars": {}, "structures": [],
          "calculations": [_calc(300, [], ["vasprun.xml"], state="running")]},
    200: {"kind": "structure", "formula": "Si2", "natoms": 2},
    400: {"kind": "process", "process_label": "WorkGraph<vasp_mlff_training>", "template": "vasp_mlff_training",
          "process_state": "finished", "exit_status": 0, "scalars": {}, "structures": [],
          "calculations": [
              _calc(401, ["dft", "SiO2_000"], ["vasprun.xml"]),
              _calc(402, ["dft", "SiO2_001", "iteration_01"], ["vasprun.xml"]),
              _calc(403, ["dft", "SiO2_001", "iteration_02"], ["vasprun.xml"]),
              _calc(404, ["dft", "Si_000"], ["vasprun.xml"]),
          ]},
    500: {"kind": "process", "process_label": "WorkGraph<vasp_band_offset>", "template": "vasp_band_offset",
          "process_state": "finished", "exit_status": 0, "scalars": {}, "structures": [],
          "calculations": [
              _calc(501, ["interface_relax", "iteration_01"], ["OUTCAR", "vasprun.xml"]),
              _calc(502, ["interface_static", "iteration_01"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
              _calc(503, ["ref_substrate_static", "iteration_01"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
              _calc(504, ["ref_film_static", "iteration_01"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
          ]},
    600: {"kind": "process", "process_label": "WorkGraph<vasp_workfunction>", "template": "vasp_workfunction",
          "process_state": "finished", "exit_status": 0, "scalars": {}, "structures": [],
          "calculations": [
              _calc(601, ["bulk_relax", "iteration_01"], ["OUTCAR", "vasprun.xml"]),
              _calc(602, ["slab_111_t1_static", "iteration_01"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
              _calc(603, ["slab_100_t1_static", "iteration_01"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
              _calc(604, ["slab_110_t1_static", "iteration_01"], ["OUTCAR", "vasprun.xml"]),
          ]},
}
#: ``(calculation pk, file name) -> bytes`` served instead of tests/data/vasprun.xml
FILES = {(pk, name): data for pk, step in ((502, "interface_static"), (503, "ref_substrate_static"),
                                           (504, "ref_film_static"))
         for name, data in band_offset_files()[step].items()}
FILES.update({(pk, name): data for pk, step in ((602, "slab_111_t1_static"), (603, "slab_100_t1_static"))
              for name, data in workfunction_files()[step].items()})
for pk, node in NODES.items():
    node.update({"schema": "sim-agent.node/1", "pk": pk, "uuid": f"{pk:08x}-1111-4000-8000-000000000000",
                 "node_type": "", "label": "", "ctime": f"2026-10-{10 + pk // 100:02d}T00:00:00+00:00"})
NODES[100]["formula"] = "Si2"
NODES[100]["chat"] = {"id": "3f2a9c1e-0000-4000-8000-000000000000", "name": "Si band structure"}
NODES[400]["chat"] = {"id": "7b1d0e44-0000-4000-8000-000000000000", "name": None}   # chat deleted
# templates sim-agent may add later, with no recipe of their own: steps named otherwise, found by shape
NODES[700] = {**NODES[600], "pk": 700, "uuid": f"{700:08x}-1111-4000-8000-000000000000",
              "template": "vasp_surface_scan", "process_label": "WorkGraph<vasp_surface_scan>", "calculations": [
                  _calc(701, ["bulk_relax"], ["OUTCAR", "vasprun.xml"]),
                  _calc(702, ["surface_a_scf"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
                  _calc(703, ["surface_b_scf"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"])]}
NODES[800] = {**NODES[500], "pk": 800, "uuid": f"{800:08x}-1111-4000-8000-000000000000",
              "template": "vasp_junction", "process_label": "WorkGraph<vasp_junction>", "calculations": [
                  _calc(801, ["junction_relax"], ["vasprun.xml"]),
                  _calc(802, ["metal_relax"], ["vasprun.xml"]),
                  _calc(803, ["oxide_relax"], ["vasprun.xml"]),
                  _calc(804, ["junction_scf"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
                  _calc(805, ["metal_scf"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
                  _calc(806, ["oxide_scf"], ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"])]}
FILES.update({(pk, name): data for pk, step in ((702, "slab_111_t1_static"), (703, "slab_100_t1_static"))
              for name, data in workfunction_files()[step].items()})
FILES.update({(pk, name): data for pk, step in ((804, "interface_static"), (805, "ref_substrate_static"),
                                                (806, "ref_film_static"))
              for name, data in band_offset_files()[step].items()})


def _astep(pk, name, kind="", flags=(), files=(), extras=None, parents=()):
    """One step of an archive as ewald-aiida describes it (``step.shape``, ``inputs``, ...)."""
    return {"name": name, "calc_id": str(pk + 90000), "calc_uuid": f"{pk:08x}-0000-4000-8000-000000000000",
            "shape": {"kind": kind, "flags": list(flags)} if kind else {}, "files": list(files),
            "inputs": {"structure": {"extras": extras or {}},
                       "parents": [{"pk": p + 90000, "via": "structure"} for p in parents]}}


PLANAR = ("OUTCAR", "vasprun.xml", "locpot_planar_avg.json")
#: node -> the steps its metadata archive holds (served as JSON; :func:`_fake_archives` opens it).
#: Archive PKs differ from the server's, as after an import elsewhere: steps match by UUID.
ARCHIVES = {
    600: [_astep(601, "bulk_relax", "relax", ["cell_relax"]),
          _astep(602, "slab_111_t1_static", "static", ["slab", "potential"], PLANAR),
          _astep(603, "slab_100_t1_static", "static", ["slab", "potential"], PLANAR),
          _astep(604, "slab_110_t1_static", "static", ["slab"], PLANAR[:2])],
    700: [_astep(701, "bulk_relax", "relax", ["cell_relax"]),
          _astep(702, "surface_a_scf", "static", ["slab", "potential"], PLANAR),
          _astep(703, "surface_b_scf", "static", ["slab", "potential"], PLANAR)],
    800: [_astep(801, "junction_relax", "relax", extras={"sim_agent_interface": {"n": 1}}),
          _astep(802, "metal_relax", "relax", extras={"sim_agent_band_offset_role": "substrate",
                                                      "sim_agent_interface": {"n": 1}}),
          _astep(803, "oxide_relax", "relax", extras={"sim_agent_band_offset_role": "film",
                                                      "sim_agent_interface": {"n": 1}}),
          _astep(804, "junction_scf", "static", ["potential"], PLANAR, parents=[801]),
          _astep(805, "metal_scf", "static", ["potential"], PLANAR, parents=[802]),
          _astep(806, "oxide_scf", "static", ["potential"], PLANAR, parents=[803])],
}

NODES[500]["ctime"] = "2026-10-01T00:00:00+00:00"   # the oldest: the listing tests above it stay as they were
NODES[600]["ctime"] = "2026-09-30T00:00:00+00:00"
NODES[700]["ctime"] = "2026-09-29T00:00:00+00:00"
NODES[800]["ctime"] = "2026-09-28T00:00:00+00:00"

EXTXYZ = ('2\nLattice="5.43 0 0 0 5.43 0 0 0 5.43" Properties=species:S:1:pos:R:3 pbc="T T T"\n'
          "Si 0 0 0\nSi 1.3575 1.3575 1.3575\n")


BASIC = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()


class Handler(BaseHTTPRequestHandler):
    requests: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if code == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="sim-agent"')
        self.end_headers()
        self.wfile.write(body)

    def _odd(self) -> bool:
        """``/moved/...`` redirects to another host; ``/portal/...`` answers a login page."""
        if self.path.startswith("/moved/"):
            self.send_response(302)
            self.send_header("Location", "http://sso.example.org/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        if self.path.startswith("/portal/"):
            self._send(200, b"<html>Sign in</html>", "text/html")
            return True
        return False

    def do_POST(self):
        Handler.requests.append(self.path)
        if self._odd():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not self.path.endswith("/api/v1/tokens"):
            return self._send(404, b'{"status": "error", "message": "no such route"}')
        if self.headers.get("Authorization") != BASIC:
            return self._send(401, b'{"status": "error", "message": "wrong password"}')
        self._send(201, json.dumps({"status": "success", "token": TOKEN, "id": "1a2b3c4d",
                                    "name": body.get("name")}).encode())

    def do_GET(self):
        Handler.requests.append(self.path)
        if self._odd():
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":  # Basic only mints tokens
            return self._send(401, b'{"status": "error", "message": "sim-agent login required"}')
        route = self.path.split("/api/v1/", 1)[1]
        if route.split("?")[0] == "nodes":
            return self._list(parse_qs(urlsplit(route).query))
        parts = route.split("/")
        if parts == ["whoami"]:
            return self._send(200, json.dumps({"user": USER, "schema": "sim-agent.node/1",
                                               "version": "test"}).encode())
        node = NODES.get(int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None
        if node is None and len(parts) > 1:      # a UUID prefix (8 characters at least)
            found = [n for n in NODES.values() if n["uuid"].startswith(parts[1])]
            node = found[0] if len(found) == 1 and len(parts[1]) >= 8 else None
        calcs = {c["pk"]: c for n in NODES.values() for c in n.get("calculations", [])}
        if len(parts) == 2 and node is not None:
            return self._send(200, json.dumps(node).encode())
        if len(parts) == 3 and parts[2].split("?")[0] == "archive.aiida" and node is not None:
            if node.get("process_state") != "finished":
                return self._send(409, b'{"status": "error", "code": "not_finished", "message": "still running"}')
            if node["pk"] not in ARCHIVES or "content=metadata" not in parts[2]:
                return self._send(404, b'{"status": "error", "message": "no archive here"}')
            return self._send(200, json.dumps(ARCHIVES[node["pk"]]).encode(), "application/zip")
        if len(parts) == 3 and parts[2] == "structure.extxyz" and node and node["kind"] == "structure":
            return self._send(200, EXTXYZ.encode(), "text/plain")
        if (len(parts) >= 4 and parts[2] == "files" and int(parts[1]) in calcs
                and "/".join(parts[3:]) in calcs[int(parts[1])]["files"]):
            name = "/".join(parts[3:])
            body = FILES.get((int(parts[1]), name)) or (DATA / "vasprun.xml").read_bytes()
            return self._send(200, body, "application/octet-stream")
        self._send(404, json.dumps({"status": "error", "message": "No node among your calculations."}).encode())


def _list(self, query):
    """``GET /nodes``: top-level processes, newest first, ``limit``/``cursor``/``q``."""
    rows = [n for n in NODES.values() if n["kind"] == "process"]
    rows.sort(key=lambda n: (n["ctime"], n["pk"]), reverse=True)
    if "cursor" in query:     # opaque to the client; here the index of the next row
        rows = rows[int(query["cursor"][0]):]
        start = int(query["cursor"][0])
    else:
        start = 0
    if "q" in query:
        q = query["q"][0].lower()
        fields = ("label", "description", "template", "formula", "process_label")
        rows = [n for n in rows if q in " ".join([str(n.get(k) or "") for k in fields]
                                                 + [str((n.get("chat") or {}).get("name") or "")]).lower()]
    limit = int(query.get("limit", ["50"])[0])
    page = rows[:limit]
    keys = ("pk", "uuid", "label", "description", "ctime", "template", "formula", "process_label",
            "process_state", "exit_status", "exit_message", "chat")
    self._send(200, json.dumps({"schema": "sim-agent.node/1", "nodes": [{k: n.get(k) for k in keys} for n in page],
                                "next_cursor": str(start + limit) if len(rows) > limit else None}).encode())


Handler._list = _list


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    Handler.requests = []
    yield f"http://127.0.0.1:{srv.server_address[1]}/alice/"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _fake_archives(monkeypatch):
    """ewald-aiida's ``open_archive`` for the fake server's archives (JSON, not AiiDA's zip:
    opening real ones is ewald-aiida's to test)."""
    from ewald.plugin_api import Workflow, WorkflowStep

    def open_archive(path):
        wf = Workflow("archive")
        for d in json.loads(Path(path).read_text(encoding="utf-8")):
            step = WorkflowStep(d.pop("name"))
            for key, value in d.items():
                setattr(step, key, value)
            wf.steps.append(step)
        return wf

    monkeypatch.setattr("ewald_simagent.shapes._open_archive", open_archive)


@pytest.fixture(autouse=True)
def _ewald_config(tmp_path, monkeypatch):
    monkeypatch.setenv("EWALD_CONFIG_DIR", str(tmp_path / "ewald-config"))
    monkeypatch.setattr("ewald.keychain._keyring", lambda: None)


@pytest.fixture
def connected(server):
    from ewald_simagent import client

    client.save_connection(client.Connection("default", server, USER, {}), TOKEN)
    return server
