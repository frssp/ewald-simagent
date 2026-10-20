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


def test_settings_page_and_tool_are_registered():
    from ewald import plugins

    assert plugins.settings_pages()["simagent"].title == "sim-agent"
    assert "simagent:1234" in plugins.tools()["simagent_node"].description


def test_ai_tool_lists_paths_to_open(connected):
    from ewald_simagent.tools import simagent_node

    out = simagent_node(None, "100")
    assert out["open"] == "simagent:100" and out["scalars"] == {"band_gap": 0.61}
    assert [s["path"] for s in out["steps"]][:3] == ["simagent:100/relax/iteration_01",
                                                     "simagent:100/relax/iteration_02", "simagent:100/scf"]
    assert out["structures"] == [{"path": "simagent:100/relaxed_structure", "formula": "Si2"}]
    assert out["steps"][2]["work_dir"] == "/scratch/alice/103" and "relaxed_structure" in out["listing"]
