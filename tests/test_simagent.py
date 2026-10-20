import pytest
from ewald import load, plugins

from ewald_simagent import Connection, SimAgentError, describe, parse, resolve
from ewald_simagent.resolve import remote_path
from ewald_simagent.uri import SimAgentURI

from .conftest import NODES, Handler


@pytest.mark.parametrize("text, uri", [
    ("simagent:1234", SimAgentURI(1234)),
    ("simagent:lab/1234", SimAgentURI(1234, (), "lab")),
    ("simagent:3F9C2E1A", SimAgentURI("3f9c2e1a")),
    ("simagent:1234/scf/CHGCAR", SimAgentURI(1234, ("scf", "CHGCAR"))),
    ("simagent:lab/8/relaxed_structure", SimAgentURI(8, ("relaxed_structure",), "lab")),
])
def test_parse(text, uri):
    assert parse(text) == uri
    assert parse(str(uri)) == uri


@pytest.mark.parametrize("bad", ["simagent:", "simagent:lab", "simagent:lab/x/1", "POSCAR"])
def test_parse_refuses(bad):
    with pytest.raises(ValueError):
        parse(bad)


def _names(targets):
    return [(t.kind, t.node["pk"], t.name, t.where) for t in targets]


class TestResolve:
    def test_default_is_the_first_steps_trajectory_joined(self):
        assert _names(resolve(NODES[100])) == [("file", 101, "vasprun.xml", "retrieved"),
                                               ("file", 102, "vasprun.xml", "retrieved")]

    def test_a_step(self):
        assert _names(resolve(NODES[100], ("bands",))) == [("file", 104, "vasprun.xml", "retrieved")]

    def test_a_restart_of_a_step(self):
        assert _names(resolve(NODES[100], ("relax", "iteration_02"))) == [
            ("file", 102, "vasprun.xml", "retrieved")]

    def test_a_retrieved_file_comes_from_the_latest_run_of_the_step(self):
        assert _names(resolve(NODES[100], ("relax", "OUTCAR"))) == [("file", 102, "OUTCAR", "retrieved")]

    def test_a_file_not_retrieved_is_read_on_the_cluster(self):
        assert _names(resolve(NODES[100], ("scf", "CHGCAR"))) == [("file", 103, "CHGCAR", "remote")]

    def test_an_output_structure(self):
        assert _names(resolve(NODES[100], ("relaxed_structure",))) == [("structure", 200, None, None)]

    def test_a_structure_node(self):
        assert _names(resolve(NODES[200])) == [("structure", 200, None, None)]

    def test_nothing_to_show_names_the_steps(self):
        desc = {**NODES[100], "calculations": [{**c, "files": ["OUTCAR"]} for c in NODES[100]["calculations"]],
                "structures": []}
        with pytest.raises(SimAgentError, match="steps: relax, iteration_01, iteration_02, scf, bands"):
            resolve(desc, ("bands",))


def test_remote_path_uses_the_login_or_a_host_alias():
    calc = NODES[100]["calculations"][2]
    assert remote_path(calc, "CHGCAR", Connection("d", "u", "a")) == "alice@hpc.example.org:/scratch/alice/103/CHGCAR"
    assert remote_path(calc, "CHGCAR", Connection("d", "u", "a", {"supercom": "hpc"})) == "hpc:/scratch/alice/103/CHGCAR"


def test_registered_as_the_simagent_scheme():
    assert plugins.scheme_reader("simagent:1") is not None


class TestLoad:
    def test_workflow_trajectory(self, connected):
        pipe = load("simagent:100")
        n = len(pipe.source.readers[0])
        assert n >= 2  # two relax runs joined
        data = pipe.compute(0)
        info = data.attributes["sim-agent"]
        assert info["pk"] == 100 and info["calc_pk"] == 101 and info["template"] == "vasp_bands"
        assert data.attributes["sim-agent"]["scalars"]["misc"]["band_gap"] == 0.61
        assert "band_gap" not in data.attributes
        assert pipe.compute(n - 1).attributes["sim-agent"]["calc_pk"] == 102

    def test_finished_files_are_cached(self, connected):
        load("simagent:100/bands")
        load("simagent:100/bands")
        assert sum(r.endswith("/104/files/vasprun.xml") for r in Handler.requests) == 1

    def test_running_calculation_is_downloaded_again_and_marked_incomplete(self, connected):
        with pytest.warns(UserWarning):
            pipe = load("simagent:300")
        assert pipe.source.readers[0].incomplete
        with pytest.warns(UserWarning):
            load("simagent:300")
        assert sum(r.endswith("/300/files/vasprun.xml") for r in Handler.requests) == 2

    def test_structure(self, connected):
        data = load("simagent:100/relaxed_structure").compute(0)
        assert data.count == 2 and data.attributes["sim-agent"]["file"] is None

    def test_other_users_or_missing_node(self, connected):
        with pytest.raises(SimAgentError, match="among your calculations"):
            load("simagent:999")

    def test_revoked_token_says_to_log_in_again(self, server):
        from ewald_simagent import client

        client.save_connection(client.Connection("default", server, "alice", {}), "sa_pat_revoked")
        with pytest.raises(SimAgentError, match="refused the saved token.*ewald_simagent login"):
            load("simagent:100")

    def test_no_connection_says_how_to_log_in(self):
        with pytest.raises(SimAgentError, match="ewald_simagent login"):
            load("simagent:100")


def test_describe(connected):
    text = describe(100)
    assert "relaxed_structure: Si2 <200>  -> simagent:default/00000064-/relaxed_structure" in text
    assert load("simagent:default/00000064-/scf").num_frames >= 1     # the stable path opens it
    assert "relax/iteration_02: VaspCalculation <102>" in text and "/scratch/alice/103" in text


def test_login_trades_the_password_for_a_token(server, monkeypatch):
    from ewald_simagent import __main__, client

    from .conftest import TOKEN

    monkeypatch.setattr("getpass.getpass", lambda prompt: "s3cret")
    __main__.main(["login", server, "--user", "alice", "--host", "supercom=hpc"])
    assert client.get_connection().hosts == {"supercom": "hpc"}
    assert client.get_connection().token() == TOKEN
    stored = client.config_path().read_text() + (client.config_path().parent / "credentials.json").read_text()
    assert "s3cret" not in stored and TOKEN not in client.config_path().read_text()
    monkeypatch.setattr("getpass.getpass", lambda prompt: "wrong")
    with pytest.raises(SystemExit):
        __main__.main(["login", server, "--user", "alice", "--name", "other"])
    assert "other" not in client.connections()


def test_login_with_a_key_from_the_web_ui(server, monkeypatch):
    from ewald_simagent import __main__, client

    from .conftest import TOKEN

    monkeypatch.setattr("getpass.getpass", lambda prompt: TOKEN + "\n")
    __main__.main(["login", server, "--user", "alice", "--key"])
    assert client.get_connection().token() == TOKEN
    monkeypatch.setattr("getpass.getpass", lambda prompt: "sa_pat_bogus")
    with pytest.raises(SystemExit):
        __main__.main(["login", server, "--user", "alice", "--key", "--name", "other"])
    assert "other" not in client.connections()


def test_redirects_and_login_pages_stop_with_a_message(server, monkeypatch):
    from ewald_simagent import client

    from .conftest import TOKEN

    base = server.replace("/alice/", "")
    for url, what in ((base + "/moved/alice/", r"redirects \(302\) to http://sso"),
                      (base + "/portal/alice/", "text/html instead of sim-agent's API")):
        conn = client.Connection("x", url, "alice")
        with pytest.raises(SimAgentError, match=what):
            client.Client(conn, token=TOKEN).whoami()
        with pytest.raises(SimAgentError, match=what):
            client.Client(conn, password="s3cret").mint_token("t")


def test_authorization_is_not_carried_by_redirects(server):
    from ewald_simagent import client

    req = []
    c = client.Client(client.Connection("x", server, "alice"), token="sa_pat_x")
    real = c._opener.open
    c._opener.open = lambda r, timeout=None: (req.append(r), real(r, timeout=timeout))[1]
    with pytest.raises(SimAgentError):
        c.whoami()
    assert req[0].unredirected_hdrs.get("Authorization") == "Bearer sa_pat_x"
    assert "Authorization" not in req[0].headers


def test_unsafe_parts_are_refused():
    from ewald_simagent.uri import parse

    for bad in ("simagent:1234/scf/../../etc/passwd", "simagent:1234//CHGCAR", "simagent:1234/./x",
                "simagent:/1234", "simagent:1234/~root", "simagent:1234/a\\b"):
        with pytest.raises(ValueError, match="every part is a name"):
            parse(bad)
    assert parse("simagent:1234/scf/CHGCAR/").parts == ("scf", "CHGCAR")


def test_separate_calculations_are_not_joined(connected):
    with pytest.warns(UserWarning, match="3 separate calculations.*simagent:400/dft/SiO2_001"):
        pipe = load("simagent:400")
    assert pipe.source.readers[0].targets[0].node["pk"] == 401 and len(pipe.source.readers[0].targets) == 1
    pipe = load("simagent:400/SiO2_001")          # one structure, its restarts joined
    assert [t.node["pk"] for t in pipe.source.readers[0].targets] == [402, 403]


def test_stable_paths_and_names():
    from ewald_simagent import client
    from ewald_simagent.uri import parse, stable

    assert stable("lab", "3F9C2E1A-0000") == "simagent:lab/3f9c2e1a"
    assert stable("lab", "12345678-9abc") == "simagent:lab/12345678-"     # never a PK
    assert parse(stable("lab", "12345678-9abc")).node == "12345678-"
    for bad in ("1234", "abcd", "a b", ""):
        with pytest.raises(SimAgentError, match="Connection name"):
            client.check_name(bad)
    assert client.plain_http("http://10.0.0.5/alice/") and not client.plain_http("http://127.0.0.1:8000/")
    assert not client.plain_http("https://server/alice/")


def test_cache_drops_the_least_recently_used(connected, monkeypatch):
    import os

    from ewald_simagent import reader

    load("simagent:100/scf")
    load("simagent:100/bands")
    root = reader.cache_dir()
    old = root / "00000067-0000-4000-8000-000000000000"      # scf (103), used first
    os.utime(old, (1, 1))
    removed = reader.trim_cache(limit=1)
    assert old in removed and len(removed) == 2
    assert reader.trim_cache(keep={"x"}, limit=1e12) == []


def test_login_warns_about_plain_http(monkeypatch):
    from ewald_simagent import __main__, client

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(SystemExit, match="Not saved"):
        __main__.main(["login", "http://10.0.0.5/alice/", "--user", "alice"])
    assert client.connections() == {}
