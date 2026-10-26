"""``python -m ewald_simagent doctor`` / the Diagnose button: each failure seen in practice names
its step and the fix."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ewald_simagent import client, diagnose

from .conftest import TOKEN, USER


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for name in ("http_proxy", "https_proxy", "no_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


def _steps(checks):
    return {c.step: c for c in checks}


def _serve(status, body, ctype, host="127.0.0.1"):
    """A web server answering every request with one fixed response (nginx with a dead backend,
    or an office proxy's block page)."""
    class Fixed(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer((host, 0), Fixed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _save(url, token=TOKEN, proxy=""):
    conn = client.Connection("default", url, USER, {}, proxy=proxy)
    client.save_connection(conn, token)
    return conn


def test_a_working_connection(connected):
    checks = diagnose.diagnose()
    steps = _steps(checks)
    assert diagnose.ok(checks)
    assert [c.step for c in checks] == ["Connection", "Address", "Proxy", "DNS", "Connect", "Backend",
                                        "Access key", "Account"]
    assert steps["Proxy"].detail.startswith("direct") and steps["DNS"].status == "skip"
    assert steps["Backend"].seconds is not None
    report = diagnose.report(checks)
    assert "logged in as alice" in report and report.endswith("Working.")


def test_no_connection_yet():
    checks = diagnose.diagnose()
    assert not diagnose.ok(checks) and checks[0].step == "Connection" and "login" in checks[0].hint


def test_no_key_on_this_pc(server):
    client.save_connection(client.Connection("default", server, USER, {}))
    step = _steps(diagnose.diagnose())["Access key"]
    assert step.status == "fail" and "keychain is per PC" in step.hint


def test_a_revoked_key(server):
    _save(server, token="sa_pat_revoked")
    step = _steps(diagnose.diagnose())["Access key"]
    assert step.status == "fail" and "expired or revoked" in step.detail and "API keys" in step.hint


def test_a_stale_backend_answers_504_and_restarting_it_is_the_fix():
    srv = _serve(504, b"<html><title>504 Gateway Time-out</title></html>", "text/html")
    _save(f"http://127.0.0.1:{srv.server_address[1]}/alice/")
    try:
        checks = diagnose.diagnose()
    finally:
        srv.shutdown()
    step = _steps(checks)["Backend"]
    assert step.status == "fail" and "504" in step.detail
    assert "systemctl restart sim-agent-backend@alice.service" in step.hint
    assert "Access key" not in _steps(checks)
    assert diagnose.report(checks).endswith("Not working: Backend failed.")


def test_a_backend_that_never_answers():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()                       # accepts the TCP connection, never answers HTTP
    try:
        _save(f"http://127.0.0.1:{listener.getsockname()[1]}/alice/")
        step = _steps(diagnose.diagnose(timeout=0.5))["Backend"]
    finally:
        listener.close()
    assert step.status == "fail" and "no answer" in step.detail and "restart" in step.hint


def test_an_office_proxy_block_page_says_connect_directly(server, monkeypatch):
    proxy = _serve(403, "<html><head><title>전사 차단</title></head></html>".encode(), "text/html; charset=utf-8")
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
    monkeypatch.setenv("http_proxy", proxy_url)
    try:
        _save(server)                                  # Proxy empty: the PC's proxy is not used
        steps = _steps(diagnose.diagnose())
        assert steps["Proxy"].status == "ok" and proxy_url in steps["Proxy"].detail
        assert "not used" in steps["Proxy"].detail and steps["Account"].status == "ok"

        _save(server, proxy="system")
        steps = _steps(diagnose.diagnose())
        assert steps["Proxy"].status == "warn" and "HTTP_PROXY" in steps["Proxy"].detail
        assert steps["Connect"].detail.startswith("TCP to the proxy")
        assert steps["Backend"].status == "fail" and "전사 차단" in steps["Backend"].detail
        assert "Advanced › Proxy" in steps["Backend"].hint and "NO_PROXY" in steps["Backend"].hint

        monkeypatch.setenv("no_proxy", "127.0.0.1")
        steps = _steps(diagnose.diagnose())
        assert steps["Proxy"].status == "ok" and "NO_PROXY" in steps["Proxy"].detail
        assert steps["Account"].status == "ok"

        _save(server, proxy=proxy_url)                 # an explicit proxy, still skipped for NO_PROXY
        assert "skipped" in _steps(diagnose.diagnose())["Proxy"].detail
        monkeypatch.delenv("no_proxy")
        steps = _steps(diagnose.diagnose())
        assert "the connection's Proxy" in steps["Proxy"].detail and steps["Backend"].status == "fail"
        assert "NO_PROXY" not in steps["Backend"].hint
    finally:
        proxy.shutdown()

    _save(server, proxy="hpc-proxy:3128")
    step = _steps(diagnose.diagnose())["Proxy"]
    assert step.status == "fail" and "http://host:port" in step.detail


def test_nothing_listening_and_a_name_that_does_not_resolve():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _save(f"http://127.0.0.1:{port}/alice/")
    step = _steps(diagnose.diagnose(timeout=2))["Connect"]
    assert step.status == "fail" and "refused" in step.detail

    _save("http://sim-agent.invalid/alice/")
    checks = diagnose.diagnose(timeout=2)
    assert checks[-1].step == "DNS" and checks[-1].status == "fail"
    assert _steps(checks)["Address"].status == "warn"            # plain http to another machine


def test_wrong_address_answers_a_login_page(server):
    _save(server.replace("/alice/", "/portal/"))
    step = _steps(diagnose.diagnose())["Backend"]
    assert step.status == "fail" and "Sign in" in step.detail and "end" in step.hint


def test_doctor_command(connected, capsys):
    from ewald_simagent.__main__ import main

    main(["doctor"])
    assert "Working." in capsys.readouterr().out
    client.remove_connection("default")
    with pytest.raises(SystemExit):
        main(["doctor"])


def test_suggested_address_adds_the_login_only_where_it_is_missing():
    fix = client.suggested_address
    assert fix("http://10.0.0.5", "alice") == "http://10.0.0.5/alice/"
    assert fix("https://sim.example.org:8443/", "alice") == "https://sim.example.org:8443/alice/"
    assert fix("http://10.0.0.5/alice/", "alice") is None
    assert fix("https://alice.sim.example.org/", "alice") is None      # subdomain routing
    assert fix("http://127.0.0.1:8000/", "alice") is None              # a backend run by hand


def test_an_address_without_the_login_says_which_address_to_use():
    """IP mode with only ``http://<ip>`` (2026-10-26): nginx answers every /api/v1 call with its
    own 404 page, and the login said nothing better than 'Not found'."""
    try:                        # not 127.0.0.1, which suggested_address leaves alone
        srv = _serve(404, b"<title>404 Not Found</title>", "text/html", host="127.0.0.2")
    except OSError:
        pytest.skip("no 127.0.0.2 loopback here")
    bare = f"http://127.0.0.2:{srv.server_address[1]}"
    try:
        with pytest.raises(client.SimAgentError, match=f"use {bare}/alice/"):
            client.login(client.Connection("default", bare, USER), key=TOKEN)
        steps = diagnose.diagnose(_save(bare), timeout=2)
        address = [c for c in steps if c.step == "Address" and f"{bare}/alice/" in c.hint]
        assert address and address[0].status == "warn"
        assert f"{bare}/alice/" in _steps(steps)["Backend"].hint
    finally:
        srv.shutdown()
