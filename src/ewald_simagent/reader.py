"""The ``simagent:`` reader: ``load("simagent:1234")``, ``load("simagent:1234/scf/CHGCAR")``."""
from __future__ import annotations

from pathlib import Path

from ewald.io.base import FrameReader

from .client import Client, SimAgentError, get_connection
from .resolve import Target, remote_path, resolve
from .uri import parse


def cache_dir() -> Path:
    from ewald.settings import config_dir

    return config_dir() / "cache" / "simagent"


#: the cache's size limit when ``simagent.json`` has no ``cache_limit_gb``
CACHE_LIMIT_GB = 10.0


def _cache_limit() -> float:
    from .client import _read_config

    try:
        return float(_read_config().get("cache_limit_gb", CACHE_LIMIT_GB)) * 1e9
    except (TypeError, ValueError):
        return CACHE_LIMIT_GB * 1e9


def trim_cache(keep=(), limit: float | None = None) -> list[Path]:
    """Delete the least recently used calculations from the cache until it fits ``limit`` bytes
    (``cache_limit_gb`` in ``simagent.json``, default 10 GB); ``keep`` (UUIDs) stay. Returns
    what was removed; finished calculations download again when they are opened."""
    import shutil

    root = cache_dir()
    if not root.is_dir():
        return []
    limit = _cache_limit() if limit is None else limit
    entries = []
    for d in root.iterdir():
        if d.is_dir():
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            entries.append((d.stat().st_mtime, size, d))
    total = sum(e[1] for e in entries)
    removed = []
    for _, size, d in sorted(entries):
        if total <= limit:
            break
        if d.name in keep:
            continue
        shutil.rmtree(d, ignore_errors=True)
        total -= size
        removed.append(d)
    return removed


def _finished(calc: dict) -> bool:
    return calc.get("process_state") in ("finished", "excepted", "killed")


def fetch(client: Client, target: Target, conn) -> str:
    """A path EWALD's readers can open for ``target``: a cached local file or ``host:/path``.

    Files of a finished calculation never change, so they are kept in the cache by UUID; a
    calculation still running is downloaded again every time.
    """
    node = target.node
    if target.kind == "structure":
        dest = cache_dir() / node["uuid"] / "structure.extxyz"
        if not dest.exists():
            client.download(f"nodes/{node['pk']}/structure.extxyz", dest)
        return str(dest)
    if target.where == "remote":
        return remote_path(node, target.name, conn)
    folder = cache_dir() / node["uuid"]
    if folder.is_dir():
        folder.touch()           # recently used: the last to be trimmed
    if _finished(node):
        dest = cache_dir() / node["uuid"] / target.name
        if dest.exists():
            return str(dest)
    else:
        dest = cache_dir() / node["uuid"] / ".live" / target.name
    client.download(f"nodes/{node['pk']}/files/{target.name}", dest)
    (cache_dir() / node["uuid"]).touch()
    return str(dest)


class SimAgentReader(FrameReader):
    """A finished sim-agent calculation by AiiDA PK or UUID (read-only):
    ``simagent:[connection/]NODE[/step...][/file]``.

    ``simagent:1234`` is the first step's trajectory (all its vasprun.xml, joined),
    ``simagent:1234/bands`` another step, ``simagent:1234/relaxed_structure`` an output
    structure, ``simagent:1234/scf/CHGCAR`` one file: retrieved files come from sim-agent, the
    rest from the cluster work directory over SSH. ``connection`` overrides the saved default.
    """

    schemes = ("simagent",)
    description = ("sim-agent calculation by AiiDA PK/UUID (simagent:1234, simagent:1234/bands, "
                   "simagent:1234/scf/CHGCAR): trajectories, structures, VASP files; read-only")

    def __init__(self, path, connection: str | None = None, **kwargs):
        from ewald.io import FileSource

        self.path = str(path)
        self.uri = parse(self.path)
        self.connection = get_connection(connection or self.uri.connection)
        client = Client(self.connection)
        self.node = client.node(self.uri.node)
        self.targets = resolve(self.node, self.uri.parts)
        self._readers, self._index = [], []
        for target in self.targets:
            local = fetch(client, target, self.connection)
            try:
                readers = FileSource(local, thermo=None, **kwargs).readers
            except FileNotFoundError:
                raise SimAgentError(f"{target.name} is not in the work directory of calculation "
                                    f"{target.node['pk']} ({local}); scratch may have been "
                                    "cleaned") from None
            for reader in readers:
                self._index += [(len(self._readers), i) for i in range(len(reader))]
                self._readers.append((target, reader))
        trim_cache(keep={t.node["uuid"] for t in self.targets})
        states = {t.node.get("process_state") for t in self.targets if t.kind == "file"}
        if states - {"finished"}:
            self._set_incomplete(f"sim-agent calculation state: {', '.join(sorted(map(str, states)))}")
        if len(self._readers) == 1:
            inner = self._readers[0][1]
            for attr in ("thermo", "electronic"):
                if getattr(inner, attr, None) is not None:
                    setattr(self, attr, getattr(inner, attr))

    def __len__(self) -> int:
        return len(self._index)

    def read(self, index: int):
        k, i = self._index[index]
        target, reader = self._readers[k]
        data = reader.read(i)
        node = target.node
        data.attributes["sim-agent"] = {
            "uri": self.path, "connection": self.connection.name, "pk": self.node["pk"],
            "uuid": self.node["uuid"], "template": self.node.get("template"),
            "label": self.node.get("label"), "process_label": self.node.get("process_label"),
            "exit_status": self.node.get("exit_status"), "file": target.name,
            "calc_pk": node["pk"] if target.kind == "file" else None,
            "step": "/".join(node.get("path", [])) or None,
            # the process's numbers by output (``{"misc": {"total_energy": ...}}``, VASP units);
            # they describe the whole workflow, not this frame, so they stay out of the top level
            "scalars": self.node.get("scalars", {}),
        }
        return data
