"""The sim-agent page of EWALD's plugin settings (Plugins side bar › Settings, Edit › Plugin
settings): log in from the GUI, the same as ``python -m ewald_simagent login``.

Registered through the ``ewald.settings_pages`` entry point, so it appears only in an EWALD
that has plugin settings pages; older ones keep the command line login.
"""
from __future__ import annotations

from typing import ClassVar

from ewald.plugin_api import SettingsField as _SettingsField

from . import client


def SettingsField(*args, advanced=False, **kw):
    """``SettingsField``, folded under Advanced where this EWALD can (``advanced`` is newer)."""
    try:
        return _SettingsField(*args, advanced=advanced, **kw)
    except TypeError:
        return _SettingsField(*args, **kw)


def _hosts(text: str) -> dict:
    hosts = {}
    for line in text.replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        computer, sep, host = (p.strip() for p in line.partition("="))
        if not sep or not computer or not host:
            raise ValueError(f"Hosts: {line!r} should read COMPUTER=HOST, e.g. supercom=hpc")
        hosts[computer] = host
    return hosts


class SimAgentSettings:
    """Log in to your sim-agent server to open your calculations by PK (simagent:1234).
    Only an access key is kept, in the OS keychain; the web password, when you use it, makes
    one key and is forgotten."""

    title = "sim-agent"
    fields = (
        SettingsField("url", "Server address", placeholder="http://server/alice/",
                      help="The address you open sim-agent at in the browser"),
        SettingsField("user", "Login", help="Your sim-agent login name"),
        SettingsField("key", "Access key", kind="secret", placeholder="sa_pat_...",
                      help="Made in sim-agent's web UI under user menu › API keys"),
        SettingsField("password", "or web password", kind="secret", placeholder="used once, never stored",
                      help="Instead of a key: ewald uses it once to make a key (180 days) and forgets it"),
        SettingsField("hosts", "SSH hosts", kind="multiline", placeholder="supercom=hpc", advanced=True,
                      help="One COMPUTER=HOST per line when a cluster's own hostname does not work "
                           "here (an ~/.ssh/config alias or an EWALD remote computer)"),
        SettingsField("allow_http", "Allow plain http", kind="bool",
                      help="An http:// address to another machine sends the key unencrypted; tick this "
                           "only when the server has no https"),
        SettingsField("ca_file", "CA file", placeholder="optional", advanced=True,
                      help="CA bundle to trust when the server's https certificate is self-signed"),
        SettingsField("name", "Connection name", default="default", advanced=True,
                      help="Keep several servers under different names; simagent:NAME/1234 picks one"),
    )
    actions: ClassVar[dict] = {"Diagnose": "diagnose", "Log out": "logout"}

    def _current(self, name: str | None = None) -> client.Connection | None:
        try:
            return client.get_connection(name or None)
        except client.SimAgentError:
            return None

    def load(self) -> dict:
        conn = self._current()
        if conn is None:
            return {}
        return {"url": conn.url, "user": conn.user, "key": conn.token() is not None, "password": False,
                "hosts": "\n".join(f"{c}={h}" for c, h in conn.hosts.items()), "name": conn.name,
                "allow_http": client.plain_http(conn.url), "ca_file": conn.ca_file}

    def save(self, values: dict) -> str:
        url, user = values["url"].strip(), values["user"].strip()
        name = values.get("name", "").strip() or "default"
        if not url.startswith(("http://", "https://")):
            raise ValueError("The server address starts with http:// or https:// (the address of sim-agent's web UI)")
        if not user:
            raise ValueError("Enter your sim-agent login")
        if client.plain_http(url) and not values.get("allow_http"):
            raise ValueError(f"{url} is plain http: the access key would cross the network unencrypted. Use "
                             "https://, or tick Allow plain http if the server has no https")
        ca_file = values.get("ca_file", "").strip()
        try:
            conn = client.Connection(client.check_name(name), url, user, _hosts(values.get("hosts", "")), ca_file)
        except client.SimAgentError as exc:
            raise ValueError(str(exc)) from None
        old = self._current(name)
        try:
            if values.get("key") or values.get("password"):
                who = client.login(conn, key=values.get("key") or None, password=values.get("password") or None)
            elif old is not None and old.name == name and old.token() and (old.url, old.user) == (conn.url, conn.user):
                client.save_connection(conn)            # only the hosts or nothing changed: keep the key
                who = client.Client(conn).whoami()
            else:
                raise ValueError("Paste an access key (sim-agent web UI: user menu › API keys) or enter "
                                 "your web password once")
        except client.SimAgentError as exc:
            raise ValueError(str(exc)) from None
        return f"Connected to {url} as {who['user']}; the access key is in the keychain"

    def diagnose(self, values: dict) -> str:
        """Every step of the saved connection, from the address to the account
        (:mod:`ewald_simagent.diagnose`), as a table with a mark per step and the fix under a
        failed one."""
        from . import diagnose

        conn = self._current(values.get("name", "").strip() or None)
        if conn is None:
            return "Not logged in yet: fill in the fields and Save"
        return diagnose.report_html(diagnose.diagnose(conn))

    test = diagnose      # the button was called Test before 0.1.1

    def logout(self, values: dict) -> str:
        name = values.get("name", "").strip() or "default"
        if name not in client.connections():
            return f"No saved connection {name!r}"
        client.remove_connection(name)
        return f"Logged out of {name!r}; revoke the key under API keys in sim-agent if it should stop working"
