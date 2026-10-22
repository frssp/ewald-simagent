"""``python -m ewald_simagent login URL --user USER`` / ``show 1234`` / ``connections`` / ``logout``."""
from __future__ import annotations

import argparse
import getpass
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ewald-simagent",
                                     description="Open sim-agent calculations in EWALD (read-only).")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="save a connection to your sim-agent backend")
    login.add_argument("url", help="your sim-agent address, e.g. http://server/alice/")
    login.add_argument("--user", required=True, help="your sim-agent login")
    login.add_argument("--key", action="store_true",
                       help="paste an access key made in the web UI (user menu > API keys) instead of "
                            "typing the web password once to make one")
    login.add_argument("--name", default="default", help="connection name (default: default)")
    login.add_argument("--allow-http", action="store_true",
                       help="save a plain http:// address to another machine without asking (the key then "
                            "crosses the network unencrypted)")
    login.add_argument("--ca-file", default="", help="CA bundle to trust for a self-signed https certificate")
    login.add_argument("--host", action="append", default=[], metavar="COMPUTER=HOST",
                       help="SSH host for a sim-agent Computer's work directories, e.g. supercom=hpc")
    show = sub.add_parser("show", help="what a node holds and the simagent: path for each part")
    show.add_argument("node", help="PK, UUID or simagent:... path")
    show.add_argument("--connection")
    sub.add_parser("connections", help="saved connections")
    logout = sub.add_parser("logout", help="forget a connection and its token (revoke it in sim-agent too)")
    logout.add_argument("name")
    args = parser.parse_args(argv)

    from . import client

    try:
        if args.command == "login":
            hosts = dict(h.split("=", 1) for h in args.host)
            conn = client.Connection(client.check_name(args.name), args.url, args.user, hosts, args.ca_file)
            if client.plain_http(args.url) and not args.allow_http:
                print(f"Warning: {args.url} is plain http, so the access key (and the password, once) cross "
                      "the network unencrypted. Use https:// if the server has it.", file=sys.stderr)
                if input("Save it anyway? [y/N] ").strip().lower() not in ("y", "yes"):
                    sys.exit("Not saved; use an https:// address, or --allow-http")
            if args.key:
                who = client.login(conn, key=getpass.getpass("sim-agent access key (sa_pat_...): "))
            else:
                who = client.login(conn, password=getpass.getpass(f"sim-agent web password for {args.user}: "))
            print(f"Saved {args.name!r}: {args.url} as {who['user']}; the access key is in the keychain, "
                  "no password is kept")
        elif args.command == "show":
            from . import describe

            print(describe(args.node, args.connection))
        elif args.command == "connections":
            for name, c in client.connections().items():
                print(f"{name}: {c.url} as {c.user}" + (f"  hosts {c.hosts}" if c.hosts else ""))
        else:
            client.remove_connection(args.name)
    except client.SimAgentError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
