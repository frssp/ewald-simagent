"""Saved sim-agent connections and the HTTP client for sim-agent's read-only node API.

A connection is the address of your own sim-agent backend (``http://server/<you>/``, the web
UI's address) and your login name. ``login`` trades the web password once for an API token
(``POST /api/v1/tokens``); only the token is kept, in the OS keychain through
:mod:`ewald.keychain` (never in ``<ewald config dir>/simagent.json``, settings.json or
``os.environ``). The contract is sim-agent's ``docs/api/results-api-v1.md`` (``sim-agent.node/1``).
Only the standard library speaks HTTP, so the plugin adds no dependency.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: The ``schema`` of sim-agent's ``/api/v1`` this plugin reads.
SCHEMA = "sim-agent.node/1"


class SimAgentError(RuntimeError):
    """A request sim-agent refused or could not answer; the message says what to do."""


@dataclass
class Connection:
    name: str
    url: str
    user: str
    #: sim-agent Computer label -> host to use for its work directories (an ``~/.ssh/config``
    #: alias or an EWALD remote profile), when the cluster's own hostname does not work here
    hosts: dict = field(default_factory=dict)

    @property
    def secret_name(self) -> str:
        return f"simagent:{self.name}"

    def token(self) -> str | None:
        from ewald.keychain import get_secret

        return get_secret(self.secret_name)[0]


def config_path() -> Path:
    from ewald.settings import config_dir

    return config_dir() / "simagent.json"


def _read_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {"default": None, "connections": {}}


def _write_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=1))


def connections() -> dict[str, Connection]:
    return {name: Connection(name=name, **{k: v for k, v in c.items() if k != "name"})
            for name, c in _read_config().get("connections", {}).items()}


def get_connection(name: str | None = None) -> Connection:
    cfg = _read_config()
    name = name or cfg.get("default")
    conns = connections()
    if not conns:
        raise SimAgentError("No sim-agent connection yet. Run: "
                            "python -m ewald_simagent login http://SERVER/USER/ --user USER")
    if name is None and len(conns) == 1:
        return next(iter(conns.values()))
    if name not in conns:
        raise SimAgentError(f"No sim-agent connection {name!r}; saved: {', '.join(conns)}")
    return conns[name]


def save_connection(conn: Connection, token: str | None = None, default: bool = True) -> None:
    cfg = _read_config()
    entry = asdict(conn)
    entry.pop("name")
    cfg.setdefault("connections", {})[conn.name] = entry
    if default or not cfg.get("default"):
        cfg["default"] = conn.name
    _write_config(cfg)
    if token:
        from ewald.keychain import set_secret

        set_secret(conn.secret_name, token)


def login(conn: Connection, key: str | None = None, password: str | None = None) -> dict:
    """Check ``conn`` with an access key (``key``) or by trading the web ``password`` once for a
    new key, then save it; returns ``whoami``. Only the key is kept, in the keychain."""
    if key:
        token = key.strip()
    elif password:
        import socket

        token = Client(conn, password=password).mint_token(f"ewald on {socket.gethostname()}")
    else:
        raise SimAgentError("Give an access key (sim-agent web UI: user menu > API keys) or the web password")
    who = Client(conn, token=token).whoami()
    if who.get("user") != conn.user:
        raise SimAgentError(f"That key belongs to {who.get('user')!r}, not {conn.user!r}")
    save_connection(conn, token)
    return who


def remove_connection(name: str) -> None:
    cfg = _read_config()
    cfg.get("connections", {}).pop(name, None)
    if cfg.get("default") == name:
        cfg["default"] = next(iter(cfg.get("connections", {})), None)
    _write_config(cfg)
    from ewald.keychain import delete_secret

    delete_secret(f"simagent:{name}")


class Client:
    """Requests to one sim-agent backend: ``Bearer`` with the saved token, or ``Basic`` with
    ``password`` (only to mint a token)."""

    def __init__(self, conn: Connection, password: str | None = None, token: str | None = None,
                 timeout: float = 60):
        self.conn = conn
        self.base = conn.url.rstrip("/") + "/api/v1/"
        self.timeout = timeout
        self._password, self._token = password, token

    def _login_hint(self) -> str:
        return f"Run: python -m ewald_simagent login {self.conn.url} --user {self.conn.user}"

    def _request(self, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data else "GET")
        if data:
            req.add_header("Content-Type", "application/json")
        if self._password is not None:
            basic = base64.b64encode(f"{self.conn.user}:{self._password}".encode()).decode()
            req.add_header("Authorization", f"Basic {basic}")
        else:
            token = self._token or self.conn.token()
            if token is None:
                raise SimAgentError(f"No sim-agent token saved for {self.conn.name!r}. {self._login_hint()}")
            req.add_header("Authorization", f"Bearer {token}")
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode()).get("message", "")
            except (OSError, ValueError, AttributeError):
                message = ""
            if exc.code == 401:
                what = "password" if self._password is not None else "saved token (expired or revoked?)"
                raise SimAgentError(f"sim-agent at {self.conn.url} refused the {what} of "
                                    f"{self.conn.user!r}. {self._login_hint()}") from None
            if exc.code == 429:
                raise SimAgentError(f"sim-agent at {self.conn.url} is refusing logins for a minute after "
                                    "several wrong passwords; wait and try again") from None
            if exc.code == 403:
                raise SimAgentError(f"sim-agent at {self.conn.url} runs without a login, so it serves "
                                    f"no API: {message}") from None
            if exc.code == 404:
                raise SimAgentError(message or f"Not found on sim-agent: {path}") from None
            raise SimAgentError(f"sim-agent answered {exc.code} for {path}: {message}") from None
        except urllib.error.URLError as exc:
            raise SimAgentError(f"Cannot reach sim-agent at {self.conn.url}: {exc.reason}") from None

    def get_json(self, path: str, body: dict | None = None) -> dict:
        with self._request(path, body) as resp:
            return json.loads(resp.read().decode())

    def mint_token(self, name: str, ttl_days: int = 180) -> str:
        """A new API token (needs the client made with ``password``)."""
        return self.get_json("tokens", {"name": name, "ttl_days": ttl_days})["token"]

    def _check(self, data: dict) -> dict:
        if data.get("schema") != SCHEMA:
            raise SimAgentError(f"sim-agent speaks {data.get('schema')!r}, this plugin reads {SCHEMA!r}; "
                                "update ewald-simagent (or sim-agent)")
        return data

    def whoami(self) -> dict:
        return self._check(self.get_json("whoami"))

    def node(self, node) -> dict:
        """sim-agent's description of one node (PK, UUID or UUID prefix)."""
        return self._check(self.get_json(f"nodes/{urllib.parse.quote(str(node))}"))

    def download(self, path: str, dest: Path) -> Path:
        """Fetch ``path`` into ``dest`` (written whole, then moved in place)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".part-")
        try:
            with self._request(path) as resp, os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(resp, out, 1 << 20)
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return dest
