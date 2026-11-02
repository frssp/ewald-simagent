"""One-click diagnosis of a sim-agent connection: each step from the address to the account, with
how long it took and what to do when it fails.

``python -m ewald_simagent doctor`` and the **Diagnose** button of the settings page run
:func:`diagnose`. The steps tell apart the problems seen in practice: a name that does not
resolve, a firewall or VPN, an office proxy that answers for the server (a 403 block page or a
504; the fix is connecting directly), a backend process that stopped answering behind nginx (504; the
fix is restarting it), and an access key that expired or was revoked. Read-only: it sends one
request without credentials and one ``whoami`` with the saved key, nothing else.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import client

#: a step slower than this (seconds) is marked slow
SLOW = 3.0

MARKS = {"ok": "✓", "warn": "!", "fail": "✗", "skip": "–"}
#: mark colors for :func:`report_html`, readable on the light and the dark theme
COLORS = {"ok": "#2da44e", "warn": "#d4a72c", "fail": "#e5534b", "skip": "#8b949e"}


@dataclass
class Check:
    """One step: ``status`` is ok, warn, fail or skip; ``hint`` says what to do about it."""

    step: str
    status: str
    detail: str
    hint: str = ""
    seconds: float | None = None

    def line(self) -> str:
        took = f" ({self.seconds * 1000:.0f} ms)" if self.seconds is not None else ""
        text = f"{MARKS[self.status]} {self.step}: {self.detail}{took}"
        return text + (f"\n    → {self.hint}" if self.hint else "")


def _restart_hint(conn: client.Connection) -> str:
    return ("The sim-agent backend behind the web server is stopped or stuck (an old process). Restart "
            f"it on the server: sudo systemctl restart sim-agent-backend@{conn.user}.service, "
            "or ask the administrator; then Diagnose again")


def route(conn: client.Connection) -> tuple[str | None, str]:
    """The proxy requests to ``conn`` go through (None: direct) and where that choice comes from,
    following :func:`client.proxies`: an empty Proxy connects directly, ``system`` makes the
    lookup urllib's default ``ProxyHandler`` makes, anything else is the proxy."""
    parts = urllib.parse.urlsplit(conn.url)
    setting = (conn.proxy or "").strip()
    if not setting:
        system = urllib.request.getproxies().get(parts.scheme)
        return None, ("Proxy is empty, so this PC's proxy " + (f"{system} " if system else "") +
                      "is not used")
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if setting != "system":        # urllib skips even a given proxy for a NO_PROXY host
        if urllib.request.proxy_bypass(netloc):
            return None, f"NO_PROXY covers {parts.hostname}, so the connection's Proxy is skipped"
        return setting, "the connection's Proxy"
    env = urllib.request.getproxies_environment()
    proxy = urllib.request.getproxies().get(parts.scheme)
    source = f"Proxy = system: {parts.scheme.upper()}_PROXY" if env.get(parts.scheme) else \
        "Proxy = system: the OS proxy settings"
    if not proxy:
        return None, "Proxy = system, and no proxy is set for this PC"
    if urllib.request.proxy_bypass(netloc):
        return None, f"Proxy = system, and NO_PROXY (or the OS bypass list) covers {parts.hostname}"
    return proxy, source


#: where the Proxy setting is
PROXY_SETTING = "Plugin settings › sim-agent › Advanced › Proxy"


def _direct_fix(conn: client.Connection) -> str:
    """How to stop going through the proxy for this server."""
    host = urllib.parse.urlsplit(conn.url).hostname
    if (conn.proxy or "").strip() == "system":
        return f"empty {PROXY_SETTING} to connect directly (or add {host} to NO_PROXY)"
    return f"empty {PROXY_SETTING} to connect directly"


def _html_title(body: bytes) -> str:
    match = re.search(rb"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
    text = (match.group(1) if match else body[:120]).decode("utf-8", "replace")
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())[:100]


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def diagnose(conn: client.Connection | str | None = None, timeout: float = 10) -> list[Check]:
    """Check the saved connection ``conn`` (a :class:`client.Connection`, a connection name, or
    None for the default) step by step; later steps are skipped once one fails."""
    checks: list[Check] = []

    def add(*args, **kw) -> Check:
        checks.append(Check(*args, **kw))
        return checks[-1]

    if not isinstance(conn, client.Connection):
        try:
            conn = client.get_connection(conn)
        except client.SimAgentError as exc:
            add("Connection", "fail", str(exc), "Save one first: Plugin settings › sim-agent, or "
                "python -m ewald_simagent login")
            return checks
    add("Connection", "ok", f"{conn.name!r}: {conn.url} as {conn.user}")

    parts = urllib.parse.urlsplit(conn.url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        add("Address", "fail", f"{conn.url!r} is not an http(s):// address",
            "Use the address you open sim-agent at in the browser, e.g. http://server/alice/")
        return checks
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        add("Address", "fail", f"{conn.url!r} has a bad port", "Fix the server address")
        return checks
    if client.plain_http(conn.url):
        add("Address", "warn", f"plain http to {host}:{port}: the access key crosses the network unencrypted",
            "Fine on a trusted site network (sim-agent's IP mode has no https); use https:// when the "
            "server has it")
    else:
        add("Address", "ok", f"{parts.scheme}://{host}:{port}")
    fixed = client.suggested_address(conn.url, conn.user)
    if fixed:
        add("Address", "warn", f"{conn.url} has no /{conn.user}/ path",
            f"sim-agent serves each user under the server's address plus the login: use {fixed}")

    try:
        client.proxies(conn)
    except client.SimAgentError as exc:
        add("Proxy", "fail", str(exc), f"Fix {PROXY_SETTING}")
        return checks
    proxy, why = route(conn)
    if proxy:
        add("Proxy", "warn", f"requests go through {proxy} ({why})",
            "An office proxy is for the internet and often blocks or cannot reach a site server (a 403 "
            f"block page or 504). If a later step fails, {_direct_fix(conn)}")
    else:
        add("Proxy", "ok", f"direct ({why})")

    # DNS: through a proxy the proxy resolves the name, so a failure here is only a warning
    if _is_ip(host):
        add("DNS", "skip", f"{host} is an IP address")
    else:
        t0 = time.perf_counter()
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addrs = sorted({i[4][0] for i in infos})
            add("DNS", "ok", f"{host} → {', '.join(addrs[:3])}", seconds=time.perf_counter() - t0)
        except OSError as exc:
            if proxy:
                add("DNS", "warn", f"{host} does not resolve on this PC ({exc}); the proxy may resolve it",
                    seconds=time.perf_counter() - t0)
            else:
                add("DNS", "fail", f"{host} does not resolve ({exc})",
                    "Check the spelling of the address, or connect to the site network/VPN; the IP "
                    "address works too", seconds=time.perf_counter() - t0)
                return checks

    # TCP: to the server, or to the proxy when one is used
    if proxy:
        p = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        target, what = (p.hostname or "", p.port or 80), "proxy"
    else:
        target, what = (host, port), "server"
    t0 = time.perf_counter()
    try:
        with socket.create_connection(target, timeout=timeout) as sock:
            took = time.perf_counter() - t0
            add("Connect", "ok", f"TCP to the {what} {target[0]}:{target[1]}", seconds=took)
            if parts.scheme == "https" and not proxy:
                ctx = ssl.create_default_context(cafile=conn.ca_file or None)
                t0 = time.perf_counter()
                try:
                    with ctx.wrap_socket(sock, server_hostname=host) as tls:
                        add("TLS", "ok", f"{tls.version()}, certificate trusted",
                            seconds=time.perf_counter() - t0)
                except ssl.SSLCertVerificationError as exc:
                    add("TLS", "fail", f"certificate not trusted ({exc.verify_message})",
                        "Give the server's CA bundle in CA file (Plugin settings › sim-agent)")
                    return checks
                except (OSError, ssl.SSLError) as exc:
                    add("TLS", "fail", f"handshake failed ({exc})",
                        "Is this port really https? Try the http:// address the browser uses")
                    return checks
    except TimeoutError:
        add("Connect", "fail", f"no answer from the {what} {target[0]}:{target[1]} in {timeout:g} s",
            "A firewall or the wrong network: connect to the site network/VPN, or check the port",
            seconds=time.perf_counter() - t0)
        return checks
    except OSError as exc:
        add("Connect", "fail", f"the {what} {target[0]}:{target[1]} refused or is unreachable ({exc})",
            "Nothing listens on that port: check the address and port, or whether the server (nginx) is up"
            if what == "server" else f"The proxy is down; or {_direct_fix(conn)}", seconds=time.perf_counter() - t0)
        return checks

    if not _backend(conn, proxy, timeout, add):
        return checks
    _account(conn, proxy, timeout, add)
    if ok(checks):              # the proxy reaches the server: nothing to fix
        for c in checks:
            if c.step == "Proxy":
                c.status, c.hint = "ok", ""
    return checks


def _backend(conn, proxy, timeout, add) -> bool:
    """One request without credentials: sim-agent's API answers 401 with its JSON error."""
    url = conn.url.rstrip("/") + "/api/v1/whoami"
    opener = client.Client(conn)._opener
    via = f" (through {proxy})" if proxy else ""
    t0 = time.perf_counter()
    try:
        with opener.open(urllib.request.Request(url), timeout=timeout) as resp:
            code, ctype, body = resp.status, resp.headers.get("Content-Type", ""), resp.read(4096)
    except urllib.error.HTTPError as exc:
        code, ctype, body = exc.code, exc.headers.get("Content-Type", ""), exc.read(4096)
    except client.SimAgentError as exc:        # a redirect
        add("Backend", "fail", str(exc), seconds=time.perf_counter() - t0)
        return False
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        took = time.perf_counter() - t0
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            add("Backend", "fail", f"connected, but no answer to /api/v1 in {timeout:g} s{via}",
                _restart_hint(conn) if not proxy else
                f"The proxy may be holding the request: {_direct_fix(conn)}. If it is already direct: "
                + _restart_hint(conn), seconds=took)
        else:
            add("Backend", "fail", f"request failed{via}: {reason}", seconds=took)
        return False
    took = time.perf_counter() - t0
    try:
        data = json.loads(body.decode()) if "json" in ctype else None
    except ValueError:
        data = None
    host = urllib.parse.urlsplit(conn.url).hostname
    if code == 401 and isinstance(data, dict):
        slow = took > SLOW
        add("Backend", "warn" if slow else "ok", f"sim-agent API answers{via}" +
            (", slowly" if slow else ""),
            "The backend starts on the first request after an idle exit, so one slow answer is "
            "normal; if it stays slow, the server is busy" if slow else "", seconds=took)
        return True
    if code in (502, 504):
        if proxy:
            hint = (f"Probably the proxy, not sim-agent: it could not reach {host}. {_direct_fix(conn)}; "
                    "if it is still 504 when direct: " + _restart_hint(conn))
        else:
            hint = _restart_hint(conn)
        add("Backend", "fail", f"{code} {'Gateway Timeout' if code == 504 else 'Bad Gateway'}{via}", hint,
            seconds=took)
        return False
    if data is None and "html" in ctype:
        title = _html_title(body)
        if proxy:
            hint = f"That page comes from the proxy (a block page), not sim-agent: {_direct_fix(conn)}"
        else:
            hint = ("Not sim-agent's API: check the address (it ends with your login, as in the browser: "
                    "http://server/alice/)" + client.address_hint(conn))
        add("Backend", "fail", f"{code}, an HTML page{via}: {title!r}", hint, seconds=took)
        return False
    if code == 403 and isinstance(data, dict):
        add("Backend", "fail", f"403: {data.get('message', '')}",
            "This sim-agent runs without a login, so it serves no API", seconds=took)
        return False
    if code == 503:
        add("Backend", "fail", f"503: {(data or {}).get('message', '')}",
            "The backend is up but its database or user setup failed; tell the administrator", seconds=took)
        return False
    if code == 404:
        add("Backend", "fail", f"404: no /api/v1 at {conn.url}",
            "The address must end with your login (http://server/alice/); or sim-agent is older than "
            "the results API." + client.address_hint(conn), seconds=took)
        return False
    add("Backend", "fail", f"unexpected answer {code} ({ctype or 'no content type'}){via}", seconds=took)
    return False


def _account(conn, proxy, timeout, add) -> None:
    """``whoami`` with the saved key."""
    token = conn.token()
    if not token:
        add("Access key", "fail", "none saved in the keychain for this connection",
            "The keychain is per PC: paste an access key (sim-agent web UI › user menu › API keys) in "
            "Plugin settings › sim-agent and Save")
        return
    t0 = time.perf_counter()
    try:
        who = client.Client(conn, token=token, timeout=timeout).whoami()
    except client.SimAgentError as exc:
        took = time.perf_counter() - t0
        text = str(exc)
        if "refused the saved token" in text:
            add("Access key", "fail", "sim-agent refused the saved key (expired or revoked)",
                "Make a new key (web UI › user menu › API keys) and paste it in Plugin settings › sim-agent",
                seconds=took)
        else:
            add("Access key", "fail", text, seconds=took)
        return
    took = time.perf_counter() - t0
    add("Access key", "ok", "accepted", seconds=took)
    if who.get("user") != conn.user:
        add("Account", "fail", f"the key belongs to {who.get('user')!r}, not {conn.user!r}",
            "Paste a key made while logged in as yourself")
        return
    add("Account", "ok", f"logged in as {who['user']}, sim-agent {who.get('version', '?')}, "
        f"{who.get('schema')}")


def ok(checks: list[Check]) -> bool:
    return not any(c.status == "fail" for c in checks)


def verdict(checks: list[Check]) -> str:
    fails = [c for c in checks if c.status == "fail"]
    if fails:
        return f"Not working: {fails[0].step} failed."
    return "Working, with warnings." if any(c.status == "warn" for c in checks) else "Working."


def report(checks: list[Check]) -> str:
    """The checks as lines, then a one-line verdict (the command line's form)."""
    return "\n".join([*(c.line() for c in checks), "", verdict(checks)])


def report_html(checks: list[Check]) -> str:
    """The checks for a Qt label, short: the steps that passed on one line, then each doubtful or
    failed step with its fix under it. Everything from the server (a page title) is escaped."""
    def mark(status):
        return f'<b style="color:{COLORS[status]}">{MARKS[status]}</b>'

    def took(c):
        return f' <span style="color:{COLORS["skip"]}">{c.seconds * 1000:.0f} ms</span>' \
            if c.seconds is not None and c.step in ("Connect", "Backend", "Access key") else ""

    passed = [c for c in checks if c.status in ("ok", "skip")]
    lines = []
    if passed:
        lines.append(mark("ok") + " " + " · ".join(html.escape(c.step) + took(c) for c in passed))
    for c in checks:
        if c.status in ("warn", "fail"):
            lines.append(f"{mark(c.status)} <b>{html.escape(c.step)}</b>: {html.escape(c.detail)}{took(c)}")
            if c.hint:
                lines.append(f'<span style="color:{COLORS[c.status]}">&nbsp;&nbsp;&nbsp;→ '
                             f"{html.escape(c.hint)}</span>")
    account = next((c for c in checks if c.step == "Account" and c.status == "ok"), None)
    color = COLORS["ok"] if ok(checks) else COLORS["fail"]
    tail = f" {html.escape(account.detail)}" if account else ""
    lines.append(f'<b style="color:{color}">{html.escape(verdict(checks))}</b>{tail}')
    return "<br>".join(lines)
