"""A fake sim-agent backend on localhost: the ``/api/v1`` routes of sim-agent's
``web/results_api.py``, answering from :data:`NODES` and serving ``tests/data/vasprun.xml``.
Payloads follow the ``sim-agent.node/1`` shape that module documents."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

DATA = Path(__file__).parent / "data"
USER, PASSWORD = "alice", "s3cret"


def _calc(pk, path, files, state="finished", remote=True):
    return {"pk": pk, "uuid": f"{pk:08x}-0000-4000-8000-000000000000", "path": path,
            "process_label": "VaspCalculation", "process_state": state, "exit_status": 0,
            "exit_message": None, "files": files, "structures": [],
            "remote": {"computer": "supercom", "hostname": "hpc.example.org", "username": "alice",
                       "path": f"/scratch/alice/{pk}"} if remote else None}


NODES = {
    100: {"kind": "process", "process_label": "WorkGraph<vasp_bands>", "template": "vasp_bands",
          "process_state": "finished", "exit_status": 0, "scalars": {"band_gap": 0.61},
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
}
for pk, node in NODES.items():
    node.update({"schema": "sim-agent.node/1", "pk": pk, "uuid": f"{pk:08x}-1111-4000-8000-000000000000",
                 "node_type": "", "label": "", "ctime": "2026-10-20T00:00:00+00:00"})

EXTXYZ = ('2\nLattice="5.43 0 0 0 5.43 0 0 0 5.43" Properties=species:S:1:pos:R:3 pbc="T T T"\n'
          "Si 0 0 0\nSi 1.3575 1.3575 1.3575\n")


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

    def do_GET(self):
        Handler.requests.append(self.path)
        good = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != good:
            return self._send(401, b'{"status": "error", "message": "sim-agent login required"}')
        parts = self.path.split("/api/v1/", 1)[1].split("/")
        if parts == ["whoami"]:
            return self._send(200, json.dumps({"user": USER, "schema": "sim-agent.node/1",
                                               "version": "test"}).encode())
        node = NODES.get(int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None
        calcs = {c["pk"]: c for n in NODES.values() for c in n.get("calculations", [])}
        if len(parts) == 2 and node is not None:
            return self._send(200, json.dumps(node).encode())
        if len(parts) == 3 and parts[2] == "structure.extxyz" and node and node["kind"] == "structure":
            return self._send(200, EXTXYZ.encode(), "text/plain")
        if (len(parts) >= 4 and parts[2] == "files" and int(parts[1]) in calcs
                and "/".join(parts[3:]) in calcs[int(parts[1])]["files"]):
            return self._send(200, (DATA / "vasprun.xml").read_bytes(), "application/octet-stream")
        self._send(404, json.dumps({"status": "error", "message": "No node among your calculations."}).encode())


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    Handler.requests = []
    yield f"http://127.0.0.1:{srv.server_address[1]}/alice/"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _ewald_config(tmp_path, monkeypatch):
    monkeypatch.setenv("EWALD_CONFIG_DIR", str(tmp_path / "ewald-config"))
    monkeypatch.setattr("ewald.keychain._keyring", lambda: None)


@pytest.fixture
def connected(server):
    from ewald_simagent import client

    client.save_connection(client.Connection("default", server, USER, {}), PASSWORD)
    return server
