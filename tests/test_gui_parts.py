"""The settings page (EWALD's Plugins side bar › Settings) and the AI tool."""
import pytest

if not hasattr(pytest.importorskip("ewald.plugin_api"), "SettingsField"):
    pytest.skip("this EWALD has no plugin settings pages", allow_module_level=True)


def _values(server, **kw):
    return {"url": server, "user": "alice", "key": "", "password": "", "hosts": "", "name": "default", **kw}


def test_settings_page_logs_in_with_the_password_once(server):
    from ewald_simagent import client
    from ewald_simagent.settings_page import SimAgentSettings

    from .conftest import TOKEN

    page = SimAgentSettings()
    assert page.load() == {}
    with pytest.raises(ValueError, match="access key"):
        page.save(_values(server))
    with pytest.raises(ValueError, match="refused the password"):
        page.save(_values(server, password="wrong"))
    with pytest.raises(ValueError, match="COMPUTER=HOST"):
        page.save(_values(server, password="s3cret", hosts="supercom"))
    assert "as alice" in page.save(_values(server, password="s3cret", hosts="supercom=hpc"))
    assert client.get_connection().token() == TOKEN and client.get_connection().hosts == {"supercom": "hpc"}
    assert "s3cret" not in client.config_path().read_text()
    loaded = page.load()
    assert loaded["key"] is True and loaded["password"] is False and loaded["hosts"] == "supercom=hpc"
    assert "as alice" in page.save({**loaded, "key": "", "password": "", "hosts": ""})   # keeps the key
    assert client.get_connection().hosts == {} and client.get_connection().token() == TOKEN
    assert "logged in as alice" in page.test(loaded)
    assert "Logged out" in page.logout(loaded) and client.connections() == {}
    assert "Not logged in" in page.test(loaded)


def test_settings_page_takes_a_key_and_checks_it(server):
    from ewald_simagent import client
    from ewald_simagent.settings_page import SimAgentSettings

    from .conftest import TOKEN

    page = SimAgentSettings()
    with pytest.raises(ValueError, match="http"):
        page.save(_values("server/alice", key=TOKEN))
    with pytest.raises(ValueError, match="refused the saved token"):
        page.save(_values(server, key="sa_pat_bogus"))
    assert "as alice" in page.save(_values(server, key=TOKEN, name="lab"))
    assert client.get_connection("lab").token() == TOKEN


def test_settings_page_and_workflow_source_are_registered():
    from ewald import plugins

    assert plugins.settings_pages()["simagent"].title == "sim-agent"
    src = plugins.workflow_sources()["simagent"]
    assert src.schemes == ("simagent",) and src.settings_page == "simagent"


def test_ai_reads_a_calculation_through_ewalds_workflow_tool(connected):
    from ewald.llm.tools import Toolset

    out = Toolset().call("describe_workflow", {"path": "simagent:100"}).content
    assert out["path"] == "simagent:default/00000064-" and out["scalars"]["band_gap"] == 0.61
    assert [s["name"] for s in out["steps"]] == ["relax", "scf", "bands"]
    assert out["steps"][0]["runs"] == 2 and out["steps"][1]["work_dir"] == "/scratch/alice/103"
    assert {"name": "relaxed_structure", "path": "simagent:default/00000064-/relaxed_structure",
            "kind": "structure", "note": "Si2"} in out["outputs"]
