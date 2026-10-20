"""``simagent:[connection/]NODE[/part/...]``: a sim-agent calculation by AiiDA PK or UUID."""
from __future__ import annotations

import re
from dataclasses import dataclass

_NODE = re.compile(r"^(\d+|[0-9a-fA-F-]{4,36})$")


@dataclass(frozen=True)
class SimAgentURI:
    """``node`` is a PK (int) or a UUID / UUID prefix (str). ``parts`` narrow it down, in order:
    an output structure's link label (``relaxed_structure``), a step of the workflow (a call
    label such as ``relax`` or ``bands``) or a file of that step (``OUTCAR``, ``CHGCAR``).
    ``connection`` None is the default saved connection."""

    node: int | str
    parts: tuple = ()
    connection: str | None = None

    def __str__(self) -> str:
        head = [self.connection] if self.connection else []
        return "simagent:" + "/".join([*head, str(self.node), *self.parts])


def parse(text: str) -> SimAgentURI:
    """Parse ``simagent:1234``, ``simagent:lab/1234``, ``simagent:3f9c2e1a/relax/CHGCAR``.

    The first part is the node when it is a number or looks like a UUID; otherwise it names
    the connection and the node follows.
    """
    if not text.lower().startswith("simagent:"):
        raise ValueError(f"not a simagent: path: {text!r}")
    parts = [p for p in text[len("simagent:"):].strip("/").split("/") if p]
    if not parts:
        raise ValueError("simagent: needs a node PK or UUID, e.g. simagent:1234")
    connection = None
    if not _NODE.match(parts[0]):
        connection, parts = parts[0], parts[1:]
        if not parts or not _NODE.match(parts[0]):
            raise ValueError(f"{text!r}: expected simagent:[connection/]PK, e.g. simagent:{connection}/1234")
    node = int(parts[0]) if parts[0].isdigit() else parts[0].lower()
    return SimAgentURI(node, tuple(parts[1:]), connection)
