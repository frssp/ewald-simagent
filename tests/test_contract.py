"""The plugin against sim-agent's own answers.

``tests/contract/sim-agent-v1.json`` holds what sim-agent's ``/api/v1`` routes answered for a
small calculation history (captured by ``tests/contract/capture.py`` from a sim-agent checkout;
nothing in it is written by hand). Three checks hang off it:

* the fake backend in ``conftest.py``, which the other tests use, answers with the same keys and
  value types, so those tests cannot pass against a server sim-agent no longer is;
* the plugin works against a replay of the captured answers (log in, list, describe, resolve,
  open, error messages);
* with ``SIM_AGENT_PYTHON`` set to the Python of a sim-agent checkout, a fresh capture still
  matches the committed one, so a change in sim-agent's answers shows up here as a failure.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ewald_simagent import client
from ewald_simagent.resolve import remote_path, resolve
from ewald_simagent.source import SimAgentWorkflows

from . import conftest

CONTRACT = Path(__file__).parent / "contract"
CAPTURED = json.loads((CONTRACT / "sim-agent-v1.json").read_text(encoding="utf-8"))
EXCHANGES = {e["name"]: e for e in CAPTURED["exchanges"]}
USER, PASSWORD, TOKEN = "alice", "s3cret", "sa_pat_TOKEN"
#: keys whose values are a map of names chosen by the data, not fixed keys (output link
#: labels, then the numbers of each output)
MAPS = {"scalars", "scalars.*"}


def _answer(name):
    return EXCHANGES[name]["json"]


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for key, value in b.items():
        out[key] = (out[key][0] | value[0], _merge(out[key][1], value[1])) if key in out else value
    return out


def shape(value, path="") -> dict:
    """``{dotted key: (JSON types seen, {...})}`` of a body, list items merged into ``key[]``."""
    out: dict = {}

    def walk(v, p):
        kind = ("number" if isinstance(v, (int, float)) and not isinstance(v, bool)
                else type(v).__name__ if v is not None else "null")
        out[p] = (out.get(p, (set(), {}))[0] | {kind}, {})
        if isinstance(v, dict):
            out[p][1]["keys"] = out[p][1].get("keys", set()) | set(v)
            for k, item in v.items():
                walk(item, f"{p}.*" if p in MAPS else (f"{p}.{k}" if p else k))
        elif isinstance(v, list):
            for item in v:
                walk(item, f"{p}[]")

    walk(value, path)
    return out


def _shape_of(bodies) -> dict:
    merged: dict = {}
    for body in bodies:
        for p, (kinds, extra) in shape(body).items():
            if p in merged:
                merged[p] = (merged[p][0] | kinds, {"keys": merged[p][1].get("keys", set())
                                                    | extra.get("keys", set())})
            else:
                merged[p] = (kinds, extra)
    return merged


def differences(fake: dict, real: dict) -> list[str]:
    """What the fake answers differently from sim-agent: keys it invents or leaves out, and
    values of a type sim-agent never sends there (``null`` included)."""
    found = []
    for p, (kinds, extra) in fake.items():
        if p not in real:
            continue                       # under a key already reported as invented
        real_kinds, real_extra = real[p]
        if not kinds <= real_kinds:
            found.append(f"{p or '<body>'}: the fake sends {sorted(kinds - real_kinds)}, "
                         f"sim-agent {sorted(real_kinds)}")
        if p in MAPS or "keys" not in extra or "keys" not in real_extra:
            continue
        for key in sorted(extra["keys"] - real_extra["keys"]):
            found.append(f"{p + '.' if p else ''}{key}: the fake sends it, sim-agent does not")
        for key in sorted(real_extra["keys"] - extra["keys"]):
            found.append(f"{p + '.' if p else ''}{key}: sim-agent sends it, the fake does not")
    return found


# ---------------------------------------------------------------------------------------------
# The fake backend speaks sim-agent's contract
# ---------------------------------------------------------------------------------------------


def _fake(server, path, method="GET", auth=f"Bearer {conftest.TOKEN}", body=None):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(server + "api/v1/" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if auth:
        req.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


_PROCESSES = ("workgraph", "workgraph by uuid prefix", "workgraph from a deleted chat",
              "running calculation", "calculation under a workgraph")
_ERRORS = ("token, wrong password", "whoami, no key", "list, bad limit", "file not retrieved",
           "another user's node", "short uuid prefix")


@pytest.mark.parametrize("what, real, fake", [
    ("whoami", ["whoami"], lambda s: [_fake(s, "whoami")]),
    ("a new token", ["token"], lambda s: [_fake(s, "tokens", "POST", conftest.BASIC, {"name": "x"})]),
    ("a page of the listing", ["list", "list, one row", "list, search by chat name"],
     lambda s: [_fake(s, "nodes?limit=50"), _fake(s, "nodes?limit=1")]),
    ("a process", _PROCESSES,
     lambda s: [_fake(s, f"nodes/{pk}") for pk, n in conftest.NODES.items() if n["kind"] == "process"]),
    ("a structure", ["structure"], lambda s: [_fake(s, "nodes/200")]),
    ("an error", _ERRORS, lambda s: [_fake(s, "whoami", auth=None), _fake(s, "nodes/999")]),
])
def test_fake_backend_answers_like_sim_agent(server, what, real, fake):
    found = differences(_shape_of(fake(server)), _shape_of([_answer(n) for n in real]))
    assert not found, f"{what}: tests/conftest.py drifted from sim-agent:\n  " + "\n  ".join(found)


def test_shape_comparison_catches_drift():
    real = _shape_of([_answer("workgraph")])
    assert differences(real, real) == []
    renamed = {**_answer("workgraph"), "calcs": _answer("workgraph")["calculations"]}
    del renamed["calculations"]
    found = differences(_shape_of([renamed]), real)
    assert "calcs: the fake sends it, sim-agent does not" in found
    assert "calculations: sim-agent sends it, the fake does not" in found
    nulled = {**_answer("workgraph"), "label": None}
    assert differences(_shape_of([nulled]), real) == ["label: the fake sends ['null'], sim-agent ['str']"]


# ---------------------------------------------------------------------------------------------
# The plugin against a replay of sim-agent's answers
# ---------------------------------------------------------------------------------------------


def _retrieved() -> dict:
    """``{calculation pk: retrieved file names}`` from every captured description."""
    return {str(c["pk"]): c["files"] for e in CAPTURED["exchanges"] if (e["json"] or {}).get("kind") == "process"
            for c in e["json"]["calculations"]}


class Replay(BaseHTTPRequestHandler):
    """Answers each request with the captured exchange for its method, path and credentials."""

    def log_message(self, *args):
        pass

    def _exchange(self, method):
        path = self.path.split("/api/v1/", 1)[1]
        auth = self.headers.get("Authorization") or ""
        basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if method == "POST":
            return EXCHANGES["token" if auth == basic else "token, wrong password"]
        if auth != f"Bearer {TOKEN}":
            return EXCHANGES["whoami, no key" if not auth else "whoami, revoked key"]
        found = [e for e in CAPTURED["exchanges"] if e["method"] == method and e["path"] == path
                 and not e["name"].startswith("whoami, ")]
        node, _, name = path.partition("/files/")
        if not found and name and name in _retrieved().get(node.split("/")[-1], ()):
            found = [EXCHANGES["retrieved file"]]   # any retrieved file a description lists
        assert found, f"no captured answer for {method} {path}; add it to tests/contract/capture.py"
        return found[0]

    def _reply(self, method):
        e = self._exchange(method)
        if e["json"] is not None:
            body = json.dumps(e["json"]).encode()
        elif "text" in e:
            body = e["text"].encode()
        else:                              # a retrieved file: a real vasprun.xml to read
            body = (conftest.DATA / "vasprun.xml").read_bytes()
        self.send_response(e["status"])
        self.send_header("Content-Type", e["content_type"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply("GET")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._reply("POST")


@pytest.fixture
def replay():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Replay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/alice/"
    srv.shutdown()


@pytest.fixture
def logged_in(replay):
    client.login(client.Connection("default", replay, USER), password=PASSWORD)
    return replay


def test_login_with_the_password_keeps_the_minted_key(replay):
    who = client.login(client.Connection("default", replay, USER), password=PASSWORD)
    assert who["user"] == USER and client.get_connection().token() == TOKEN


def test_login_refusals(replay):
    with pytest.raises(client.SimAgentError, match="refused the password"):
        client.login(client.Connection("default", replay, USER), password="nope")
    with pytest.raises(client.SimAgentError, match="refused the saved token"):
        client.login(client.Connection("default", replay, USER), key="sa_pat_revoked")


def test_listing_names_each_chat(logged_in):
    page = SimAgentWorkflows().list()
    rows = {wf.kind: wf for wf in page.workflows}
    assert rows["vasp_bands"].group == "Si band structure" and rows["vasp_bands"].formula == "Si2"
    assert rows["vasp_workfunction"].group == "chat t-gone"     # the chat was deleted
    assert rows["VaspCalculation"].group == "" and rows["VaspCalculation"].state == "running"
    assert all(wf.path.startswith("simagent:default/cafe") for wf in rows.values())


def test_search_and_pages(logged_in):
    api = client.Client(client.get_connection())
    assert [n["template"] for n in api.nodes(q="band")["nodes"]] == ["vasp_bands"]
    first = api.nodes(limit=1)
    second = api.nodes(limit=1, cursor=first["next_cursor"])
    assert first["nodes"][0]["pk"] != second["nodes"][0]["pk"]


def test_describe_a_workgraph(logged_in):
    wf = SimAgentWorkflows().describe(str(_answer("workgraph")["pk"]))
    steps = {s.name: s for s in wf.steps}
    assert list(steps) == ["relax", "scf", "bands"] and steps["relax"].runs == 2
    assert wf.scalars == {"band_gap": 0.61, "total_energy": -10.8}
    assert wf.group == "Si band structure"
    assert [o.name for o in wf.outputs] == ["relaxed_structure"]
    assert "locpot_planar_avg.json" in [o.name for o in steps["bands"].outputs]
    by_prefix = SimAgentWorkflows().describe(wf.path)       # the stable path a notebook keeps
    assert [s.name for s in by_prefix.steps] == list(steps)


def test_resolve_a_workgraph():
    desc = _answer("workgraph")
    relax = resolve(desc)
    assert [t.node["path"] for t in relax] == [["relax", "iteration_01"], ["relax", "iteration_02"]]
    (chgcar,) = resolve(desc, ("scf", "CHGCAR"))
    assert chgcar.where == "remote"
    assert remote_path(chgcar.node, "CHGCAR", client.Connection("d", "u", USER)) == \
        f"alice@hpc.example.org:{chgcar.node['remote']['path']}/CHGCAR"
    assert resolve(desc, ("relaxed_structure",))[0].kind == "structure"
    assert resolve(_answer("structure"))[0].kind == "structure"


def test_open_a_workgraph_and_a_structure(logged_in):
    from ewald import load

    pk = _answer("workgraph")["pk"]
    pipe = load(f"simagent:{pk}")
    assert len(pipe.source.readers[0]) >= 2
    info = pipe.compute(0).attributes["sim-agent"]
    assert info["pk"] == pk and info["template"] == "vasp_bands" and info["step"] == "relax/iteration_01"
    data = load(f"simagent:{pk}/relaxed_structure").compute(0)
    assert data.count == 2


def test_running_calculation_opens_as_incomplete(logged_in):
    desc = _answer("running calculation")
    assert desc["calculations"][0]["files"] == [] and desc["calculations"][0]["remote"]
    wf = SimAgentWorkflows().describe(str(desc["pk"]))
    assert wf.state == "running" and wf.work_dir == desc["calculations"][0]["remote"]["path"]


@pytest.mark.parametrize("node, message", [
    ("another user's node", "among your calculations"),
    ("no such node", "among your calculations"),
    ("short uuid prefix", "UUID prefix of at least 8"),
])
def test_refusals_reach_the_user(logged_in, node, message):
    path = EXCHANGES[node]["path"].split("/", 1)[1]
    with pytest.raises(client.SimAgentError, match=message):
        client.Client(client.get_connection()).node(path)


def test_the_plugin_reads_the_schema_sim_agent_sends():
    assert CAPTURED["schema"] == client.SCHEMA
    assert all(_answer(n)["schema"] == client.SCHEMA for n in ("whoami", "list", "workgraph", "structure"))


# ---------------------------------------------------------------------------------------------
# The capture is still what sim-agent answers
# ---------------------------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("SIM_AGENT_PYTHON"),
                    reason="set SIM_AGENT_PYTHON to the Python of a sim-agent checkout")
def test_capture_matches_sim_agent():
    result = subprocess.run([os.environ["SIM_AGENT_PYTHON"], "-I", "-W", "ignore",
                             str(CONTRACT / "capture.py"), "--check"],
                            check=False, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert result.returncode == 0, (
        "sim-agent's /api/v1 answers changed. Run tests/contract/capture.py with that Python, "
        "read the diff of tests/contract/sim-agent-v1.json, then fix the plugin and "
        f"tests/conftest.py.\n{result.stdout[-4000:]}{result.stderr[-1000:]}")

