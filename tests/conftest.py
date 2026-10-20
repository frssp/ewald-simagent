"""A fake sim-agent backend on localhost: the node API of sim-agent's
``docs/api/node-api-v1.md`` (``sim-agent.nodes/1``), answering from :data:`NODES` and serving
``tests/data/vasprun.xml``. Keep it matching that document."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

DATA = Path(__file__).parent / "data"
USER, PASSWORD, TOKEN = "alice", "s3cret", "sa_pat_test"
API = "sim-agent.nodes/1"


def _uuid(pk):
    return f"{pk:08x}-1111-4000-8000-000000000000"


def _summary(pk, cls, kind, **extra):
    return {"api": API, "pk": pk, "uuid": _uuid(pk), "class": cls, "node_type": f"{kind}.{cls}.",
            "kind": kind, "label": "", "description": "", "ctime": "2026-10-20T00:00:00+00:00",
            "inputs": [], "outputs": [], "links_truncated": False, "files": [], **extra}


def _proc(label, state="finished", **extra):
    return {"process_label": label, "process_state": state, "exit_status": 0 if state == "finished" else None,
            "exit_message": None, "sealed": state == "finished", "caller_pk": None, "template": None, **extra}


def _called(pk, link, cls="CalcJobNode", label="VaspCalculation"):
    return {"pk": pk, "uuid": _uuid(pk), "link_label": link, "label": "", "class": cls,
            "process_label": label, "process_state": "finished", "exit_status": 0}


def _calc(pk, state="finished"):
    remote = {"computer": "supercom", "hostname": "hpc.example.org", "username": "alice",
              "path": f"/scratch/alice/{pk}"}
    return _summary(pk, "CalcJobNode", "process",
                    process=_proc("VaspCalculation", state, retrieved_pk=pk + 1000, remote=remote))


def _folder(pk, names):
    return _summary(pk, "FolderData", "data", files=[{"path": n, "size": 10} for n in names], data={})


NODES = {
    100: _summary(100, "WorkGraphNode", "process",
                  outputs=[{"link_label": "relaxed_structure", "link_type": "return", "pk": 200,
                            "uuid": _uuid(200), "class": "StructureData", "node_type": ""},
                           {"link_label": "misc", "link_type": "return", "pk": 150, "uuid": _uuid(150),
                            "class": "Dict", "node_type": ""}],
                  process=_proc("WorkGraph<vasp_bands>", template="vasp_bands",
                                called=[_called(110, "relax", "WorkChainNode", "VaspWorkChain"),
                                        _called(103, "scf"), _called(104, "bands")])),
    110: _summary(110, "WorkChainNode", "process",
                  process=_proc("VaspWorkChain", called=[_called(101, "iteration_01"),
                                                         _called(102, "iteration_02")])),
    150: _summary(150, "Dict", "data", data={"dict": {"band_gap": 0.61, "note": "x", "ok": True}}),
    200: _summary(200, "StructureData", "data",
                  data={"structure": {"formula": "Si2", "num_sites": 2, "cell": [], "pbc": [True] * 3}}),
    **{pk: _calc(pk) for pk in (101, 102, 103, 104)},
    **{pk + 1000: _folder(pk + 1000, ["OUTCAR", "vasprun.xml"]) for pk in (101, 102, 103)},
    1104: _folder(1104, ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]),
    300: _calc(300, state="running"),
    1300: _folder(1300, ["vasprun.xml"]),
}

EXTXYZ = ('2\nLattice="5.43 0 0 0 5.43 0 0 0 5.43" Properties=species:S:1:pos:R:3 pbc="T T T"\n'
          "Si 0 0 0\nSi 1.3575 1.3575 1.3575\n")


class Handler(BaseHTTPRequestHandler):
    requests: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message):
        self._send(code, {"status": "error", "message": message})

    def do_POST(self):
        Handler.requests.append(("POST", self.path))
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if not self.path.endswith("/api/v1/tokens"):
            return self._error(404, "no such route")
        if self.headers.get("Authorization") != basic:
            return self._error(401, "wrong password")
        self._send(201, {"status": "success", "token": TOKEN, "id": "1a2b3c4d", "name": body.get("name")})

    def do_GET(self):
        Handler.requests.append(("GET", self.path))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._error(401, "missing, unknown, revoked or expired token")
        route, _, query = self.path.split("/api/v1/", 1)[1].partition("?")
        parts = route.split("/")
        if parts == ["whoami"]:
            return self._send(200, {"status": "success", "api": API, "user": USER})
        node = NODES.get(int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None
        if node is None:
            return self._error(404, f"No node {parts[1] if len(parts) > 1 else ''} among your calculations.")
        if len(parts) == 2:
            return self._send(200, {"status": "success", "node": node})
        if parts[2] == "structure" and node["class"] == "StructureData" and query == "format=extxyz":
            return self._send(200, EXTXYZ.encode(), "chemical/x-extxyz")
        if parts[2] == "files" and "/".join(parts[3:]) in [f["path"] for f in node["files"]]:
            return self._send(200, (DATA / "vasprun.xml").read_bytes(), "application/octet-stream")
        self._error(404, "No such file.")


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

    client.save_connection(client.Connection("default", server, USER, {}), TOKEN)
    return server
