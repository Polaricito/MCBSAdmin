"""Command-line interface for MCBSAdmin.

Usage:
    mcbsadmin           Launch the TUI (default).
    mcbsadmin tui       Explicit TUI launch.
    mcbsadmin install   Install the latest Bedrock server build.
    mcbsadmin status    Show install/config status.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from . import __version__
from .config import Config


def _curses_wrapper():
    import curses

    try:
        from .tui import App

        def _main(stdscr):
            config = Config()
            app = App(stdscr, config)
            app.run()

        curses.wrapper(_main)
    except ImportError:
        sys.stderr.write("curses is not available on this platform.\n")
        return 1
    return 0


def _progress(done: int, total: int) -> None:
    if total:
        pct = done / total * 100
        bar = "#" * int(pct // 2)
        msg = f"  \rDownloading [{bar:<50}] {pct:5.1f}%"
    else:
        msg = f"  \rDownloading {done / 1048576:.1f} MiB"
    sys.stderr.write(msg)
    sys.stderr.flush()
    if done == total and total:
        sys.stderr.write("\n")


def _apply_data_dir(data_dir: Optional[str]) -> None:
    """Point config and the default server dir at an explicit location.

    Prevents a system-installed app (``/usr/bin`` + ``/usr/share``) from
    ever writing into the install tree: the user picks a writable spot and
    both the JSON config and server data land there.
    """
    if not data_dir:
        return
    dd = os.path.abspath(os.path.expanduser(data_dir))
    try:
        os.makedirs(dd, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"--data-dir not usable: {exc}\n")
        raise SystemExit(2)
    os.environ.setdefault("MCBSADMIN_DATA_DIR", dd)
    os.environ.setdefault("MCBSADMIN_CONFIG", os.path.join(dd, "config.json"))


def cmd_install(config: Config) -> int:
    from .server import ServerManager
    from .util import LogBuffer
    from .versions import install_bedrock

    server_dir = config.server_dir()
    os.makedirs(server_dir, exist_ok=True)

    print(f"MCBSAdmin: installing the latest Bedrock build into {server_dir}")
    try:
        v_id = install_bedrock(server_dir, progress=_progress, timeout=300.0)
    except Exception as exc:  # noqa: BLE001
        print(f"Install failed: {exc}")
        return 1
    config.set("version", v_id)
    print(f"Installed Bedrock {v_id}")
    # Write server.properties so the server can be started right away.
    mgr = ServerManager(config, LogBuffer())
    mgr.setup_files()
    print("Wrote server.properties.")
    print("Launch the manager with 'mcbsadmin' and press 'S' to start.")
    return 0


def cmd_status(config: Config) -> int:
    server_dir = config.server_dir()
    binary = os.path.join(server_dir, "bedrock_server")
    data_dir = os.path.dirname(os.path.abspath(config.path))
    print(f"data dir      : {data_dir}")
    print(f"config        : {config.path}")
    print(f"server dir    : {server_dir}")
    print(f"version       : {config.get('version') or 'not installed'}")
    print(f"bedrock_server: {'present' if os.path.exists(binary) else 'missing'}")
    print(f"port          : {config.get('gameport', 19132)} (UDP)")
    print(f"port (IPv6)   : {config.get('gameportv6', 19133)} (UDP)")
    world = config.get("world") or {}
    print(f"motd          : {config.get('motd')}")
    print(
        f"max players   : {world.get('max-players') or '10 (default)'}"
    )
    if not os.path.isdir(server_dir):
        try:
            os.makedirs(server_dir, exist_ok=True)
        except OSError:
            pass
    if not os.access(server_dir, os.W_OK):
        print(
            f"! server dir is NOT writable ({server_dir}) — "
            "use --data-dir to point MCBSAdmin at a writable location."
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcbsadmin",
        description="Terminal-based Minecraft Bedrock server manager.",
    )
    parser.add_argument("-v", "--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p_tui = sub.add_parser("tui", help="Run the interactive TUI (default)")
    p_tui.add_argument("--config", help="Path to a custom config JSON")
    p_tui.add_argument("--data-dir", help="Directory for config + server data")

    p_install = sub.add_parser(
        "install", help="Install the latest Bedrock server build"
    )
    p_install.add_argument("--config", help="Path to a custom config JSON")
    p_install.add_argument(
        "--data-dir", help="Directory for config + server data"
    )

    p_status = sub.add_parser("status", help="Show install/config status")
    p_status.add_argument("--config", help="Path to a custom config JSON")
    p_status.add_argument("--data-dir", help="Directory for config + server data")

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "config", None):
        os.environ.setdefault("MCBSADMIN_CONFIG", args.config)
    _apply_data_dir(getattr(args, "data_dir", None))

    command = args.command or "tui"
    if command == "tui":
        return _curses_wrapper()
    if command == "install":
        return cmd_install(Config())
    if command == "status":
        return cmd_status(Config())
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
