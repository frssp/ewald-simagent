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


def _serve(status, body, ctype):
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

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fixed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _save(url, token=TOKEN):
    conn = client.Connection("default", url, USER, {})
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


def test_an_office_proxy_block_page_says_no_proxy(server, monkeypatch):
    proxy = _serve(403, "<html><head><title>전사 차단</title></head></html>".encode(), "text/html; charset=utf-8")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.server_address[1]}")
    _save(server)
    try:
        steps = _steps(diagnose.diagnose())
    finally:
        proxy.shutdown()
    assert steps["Proxy"].status == "warn" and "HTTP_PROXY" in steps["Proxy"].detail
    assert steps["Connect"].detail.startswith("TCP to the proxy")
    assert steps["Backend"].status == "fail" and "전사 차단" in steps["Backend"].detail
    assert "NO_PROXY" in steps["Backend"].hint

    monkeypatch.setenv("no_proxy", "127.0.0.1")
    steps = _steps(diagnose.diagnose())
    assert steps["Proxy"].status == "ok" and "NO_PROXY" in steps["Proxy"].detail
    assert steps["Account"].status == "ok"


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
