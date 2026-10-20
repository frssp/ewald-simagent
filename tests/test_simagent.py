import pytest
from ewald import load, plugins

from ewald_simagent import Connection, SimAgentError, describe, parse, resolve
from ewald_simagent.resolve import remote_path
from ewald_simagent.uri import SimAgentURI

from .conftest import NODES, Handler


@pytest.mark.parametrize("text, uri", [
    ("simagent:1234", SimAgentURI(1234)),
    ("simagent:lab/1234", SimAgentURI(1234, (), "lab")),
    ("simagent:3F9C2E1A-0000-4000-8000-00000000ABCD", SimAgentURI("3f9c2e1a-0000-4000-8000-00000000abcd")),
    ("simagent:1234/scf/CHGCAR", SimAgentURI(1234, ("scf", "CHGCAR"))),
    ("simagent:lab/8/relaxed_structure", SimAgentURI(8, ("relaxed_structure",), "lab")),
])
def test_parse(text, uri):
    assert parse(text) == uri
    assert parse(str(uri)) == uri


@pytest.mark.parametrize("bad", ["simagent:", "simagent:lab", "simagent:lab/x/1", "POSCAR",
                                 "simagent:lab/3f9c2e1a"])
def test_parse_refuses(bad):
    with pytest.raises(ValueError):
        parse(bad)


def _names(targets):
    return [(t.kind, t.node["pk"], t.name, t.where) for t in targets]


@pytest.fixture
def described(connected):
    from ewald_simagent import Client, collect, get_connection

    return collect(Client(get_connection()), 100)


def test_collect_walks_the_workflow(described):
    assert described["template"] == "vasp_bands" and described["scalars"] == {"band_gap": 0.61}
    assert [(c["pk"], c["path"]) for c in described["calculations"]] == [
        (101, ["relax", "iteration_01"]), (102, ["relax", "iteration_02"]), (103, ["scf"]), (104, ["bands"])]
    assert described["calculations"][3]["files"] == ["OUTCAR", "vasprun.xml", "locpot_planar_avg.json"]
    assert described["structures"] == [{"link": "relaxed_structure", "pk": 200, "uuid": NODES[200]["uuid"]}]


def test_collect_falls_back_to_the_process_label_without_link_labels(connected, monkeypatch):
    from ewald_simagent import Client, collect, get_connection

    called = [{**c, "link_label": None} for c in NODES[100]["process"]["called"]]
    monkeypatch.setitem(NODES[100], "process", {**NODES[100]["process"], "called": called})
    paths = [c["path"][0] for c in collect(Client(get_connection()), 100)["calculations"]]
    assert paths == ["VaspWorkChain", "VaspWorkChain", "VaspCalculation", "VaspCalculation"]


class TestResolve:
    def test_default_is_the_first_steps_trajectory_joined(self, described):
        assert _names(resolve(described)) == [("file", 101, "vasprun.xml", "retrieved"),
                                              ("file", 102, "vasprun.xml", "retrieved")]

    def test_a_step(self, described):
        assert _names(resolve(described, ("bands",))) == [("file", 104, "vasprun.xml", "retrieved")]

    def test_a_restart_of_a_step(self, described):
        assert _names(resolve(described, ("relax", "iteration_02"))) == [
            ("file", 102, "vasprun.xml", "retrieved")]

    def test_a_retrieved_file_comes_from_the_latest_run_of_the_step(self, described):
        assert _names(resolve(described, ("relax", "OUTCAR"))) == [("file", 102, "OUTCAR", "retrieved")]

    def test_a_file_not_retrieved_is_read_on_the_cluster(self, described):
        assert _names(resolve(described, ("scf", "CHGCAR"))) == [("file", 103, "CHGCAR", "remote")]

    def test_an_output_structure(self, described):
        assert _names(resolve(described, ("relaxed_structure",))) == [("structure", 200, None, None)]

    def test_a_structure_node(self, connected):
        from ewald_simagent import Client, collect, get_connection

        assert _names(resolve(collect(Client(get_connection()), 200))) == [("structure", 200, None, None)]

    def test_nothing_to_show_names_the_steps(self, described):
        desc = {**described, "structures": [],
                "calculations": [{**c, "files": ["OUTCAR"]} for c in described["calculations"]]}
        with pytest.raises(SimAgentError, match="steps: relax, iteration_01, iteration_02, scf, bands"):
            resolve(desc, ("bands",))


def test_remote_path_uses_the_login_or_a_host_alias(described):
    calc = described["calculations"][2]
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
        assert data.attributes["band_gap"] == 0.61
        assert pipe.compute(n - 1).attributes["sim-agent"]["calc_pk"] == 102

    def test_finished_files_are_cached(self, connected):
        load("simagent:100/bands")
        load("simagent:100/bands")
        assert sum(r[1].endswith("/1104/files/vasprun.xml") for r in Handler.requests) == 1

    def test_running_calculation_is_downloaded_again_and_marked_incomplete(self, connected):
        with pytest.warns(UserWarning):
            pipe = load("simagent:300")
        assert pipe.source.readers[0].incomplete
        with pytest.warns(UserWarning):
            load("simagent:300")
        assert sum(r[1].endswith("/1300/files/vasprun.xml") for r in Handler.requests) == 2

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
    assert "relaxed_structure: StructureData <200>  -> simagent:100/relaxed_structure" in text
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
