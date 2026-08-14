"""Server process control: launch, stop, restart, console I/O.

Launches the Bedrock Dedicated Server binary (``bedrock_server``),
streams its stdout into a shared LogBuffer, parses the Bedrock console
join/leave lines for a player list, and provides console-command
execution via the server's stdin.

Bedrock has no RCON, so player tracking relies on the console log lines
("Player connected/Spawned/disconnected"). The player count comes from
``server.properties`` (``max-players``).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from typing import Dict, List, Optional, Set

from .config import Config
from .util import Event, LogBuffer

# Bedrock console log lines (modern BDS, prefixed with a timestamp):
#   [2026-08-13 15:04:05:123 INFO] Player connected: Steve, xuid: 2535452973466207
#   [2026-08-13 15:04:06:456 INFO] Player Spawned: Steve, xuid: 2535452973466207
#   [2026-08-13 15:30:00:000 INFO] Player disconnected: Steve, xuid: 2535452973466207
PLAYER_JOIN_RE = re.compile(r"Player (?:connected|Spawned): (.+?), xuid:")
PLAYER_LEAVE_RE = re.compile(r"Player disconnected: (.+?), xuid:")


def read_properties(path: str) -> Dict[str, str]:
    """Parse a server.properties file into a {key: value} dict."""
    result: Dict[str, str] = {}
    try:
        with open(path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    result[k.strip()] = v.strip()
    except OSError:
        pass
    return result


def set_property(path: str, key: str, value: str) -> None:
    """Set one key in a properties file, preserving the other lines.

    Used for single switches (e.g. allow-list) without rewriting the whole
    file; creates the file with just that key if it does not exist yet."""
    lines = []
    try:
        with open(path, "r") as fh:
            lines = fh.readlines()
    except OSError:
        pass
    pattern = re.compile(r"^" + re.escape(key) + r"\s*=")
    found = False
    for i, line in enumerate(lines):
        if pattern.match(line.lstrip()):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.writelines(lines)


def read_allowlist_file(path: str) -> List[str]:
    """Player names from an allowlist.json (usable while stopped)."""
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    names = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and entry.get("name"):
                names.append(entry["name"])
    return names


def write_allowlist_file(path: str, names: List[str]) -> None:
    """Write allowlist.json entries (XUID is filled in by the server)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    entries = [
        {"name": n, "ignoresPlayerLimit": False, "permission": "member"}
        for n in names
    ]
    with open(path, "w") as fh:
        json.dump(entries, fh)


def add_allowlist_entry(path: str, name: str) -> bool:
    names = read_allowlist_file(path)
    if name in names:
        return False
    names.append(name)
    write_allowlist_file(path, names)
    return True


def remove_allowlist_entry(path: str, name: str) -> bool:
    names = read_allowlist_file(path)
    if name not in names:
        return False
    names.remove(name)
    write_allowlist_file(path, names)
    return True


class ServerManager:
    """Owns the bedrock_server subprocess and its threads."""

    def __init__(self, config: Config, log: LogBuffer) -> None:
        self.config = config
        self.log = log
        self.proc: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self.started_at: Optional[float] = None
        self.status = "stopped"  # stopped | starting | running | stopping
        self.last_message: str = ""

        # player tracking (from Bedrock console lines)
        self.players: Set[str] = set()
        self.player_ips: Dict[str, str] = {}
        self.max_players: Optional[int] = None
        self.players_lock = threading.Lock()

        # events
        self.on_status = Event("status")
        self.on_player_change = Event("players")
        self.on_stats = Event("stats")

        self._io_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._manual_stop = False

    # ------------------------------------------------------------------
    def setup_files(self) -> None:
        """Write the server.properties that our tool needs."""
        server_dir = self._server_dir()
        os.makedirs(server_dir, exist_ok=True)
        props = os.path.join(server_dir, "server.properties")
        self._write_properties(props)
        self._load_max_players()

    def _load_max_players(self) -> None:
        """max-players from server.properties (Bedrock default is 10)."""
        props = read_properties(os.path.join(self._server_dir(), "server.properties"))
        try:
            self.max_players = int(props.get("max-players", 10))
        except (TypeError, ValueError):
            self.max_players = 10

    def _write_properties(self, path: str) -> None:
        defaults = {
            "server-name": self.config.get("motd", "MCBSAdmin managed server"),
            "server-port": str(self.config.get("gameport", 19132)),
            "server-portv6": str(self.config.get("gameportv6", 19133)),
            "level-name": str(self.config.get("level") or "level"),
            "gamemode": "survival",
            "difficulty": "normal",
            "max-players": "10",
            "online-mode": "true",
            "allow-list": "false",
            "allow-cheats": "false",
            "pvp": "true",
            "view-distance": "32",
            "tick-distance": "4",
            "enable-lan-visibility": "true",
            "default-player-permission-level": "member",
        }
        existing = read_properties(path)
        # settings we manage always reflect the current config (e.g. a new
        # motd set from the settings screen must be written, not ignored)
        existing.update(defaults)
        # world options managed from the config (World Options menu) always
        # win; anything not listed there keeps its existing value
        existing.update(
            {k: str(v) for k, v in (self.config.get("world") or {}).items()}
        )
        lines = ["#Minecraft Bedrock server properties\n", "#Generated by MCBSAdmin\n"]
        for k, v in sorted(existing.items()):
            lines.append(f"{k}={v}\n")
        with open(path, "w") as fh:
            fh.writelines(lines)

    # ------------------------------------------------------------------
    def _server_dir(self) -> str:
        return self.config.server_dir()

    def _launch_env(self, server_dir: str) -> Dict[str, str]:
        """BDS needs its shared libraries next to the binary (LD_LIBRARY_PATH)."""
        env = dict(os.environ)
        if env.get("LD_LIBRARY_PATH"):
            env["LD_LIBRARY_PATH"] = server_dir + ":" + env["LD_LIBRARY_PATH"]
        else:
            env["LD_LIBRARY_PATH"] = server_dir
        return env

    # ------------------------------------------------------------------
    def start(self) -> bool:
        if self.proc and self.proc.poll() is None:
            return False
        self._manual_stop = False
        self._stop_flag.clear()

        self.setup_files()
        server_dir = self._server_dir()
        binary = os.path.join(server_dir, "bedrock_server")
        if not os.path.exists(binary):
            self.status = "stopped"
            self.last_message = (
                "bedrock_server missing. Run 'mcbsadmin install'."
            )
            self.on_status.fire(self.status, self.last_message)
            return False

        cmd = [binary]
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=server_dir,
                env=self._launch_env(server_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
        except OSError as exc:
            self.status = "stopped"
            self.last_message = f"Failed to launch bedrock_server: {exc}"
            self.on_status.fire(self.status, self.last_message)
            return False

        self.pid = self.proc.pid
        self.started_at = time.time()
        self.status = "starting"
        self.log.clear()
        self.on_status.fire(self.status, "Starting server…")

        self._io_thread = threading.Thread(target=self._io_loop, daemon=True)
        self._io_thread.start()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        return True

    # ------------------------------------------------------------------
    def _io_loop(self) -> None:
        # snapshot the stream so a concurrent _cleanup() (which nulls
        # self.proc) can't race 'self.proc.stdout' and crash with
        # AttributeError: 'NoneType' object has no attribute 'stdout'
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        stdout = proc.stdout
        for line in iter(stdout.readline, ""):
            if self._stop_flag.is_set():
                break
            self._handle_line(line.rstrip("\n"))
        try:
            stdout.close()
        except OSError:
            pass

    def _handle_line(self, raw: str) -> None:
        self.log.append(raw)
        self._detect_player_events(raw)
        lower = raw.lower()
        if self.status == "starting" and "server started" in lower:
            self.status = "running"
            self.last_message = "Server ready."
            self.on_status.fire(self.status, self.last_message)

    def _detect_player_events(self, raw: str) -> None:
        """Track joins/leaves from the Bedrock console lines."""
        m = PLAYER_JOIN_RE.search(raw)
        if m:
            name = m.group(1).strip()
            if name:
                with self.players_lock:
                    self.players.add(name)
                    self.on_player_change.fire(sorted(self.players))
            return
        m = PLAYER_LEAVE_RE.search(raw)
        if m:
            name = m.group(1).strip()
            if name:
                with self.players_lock:
                    self.players.discard(name)
                    self.player_ips.pop(name, None)
                    self.on_player_change.fire(sorted(self.players))

    # ------------------------------------------------------------------
    def _monitor_loop(self) -> None:
        while not self._stop_flag.is_set():
            time.sleep(1.0)
            proc = self.proc
            if proc is None:
                continue
            if proc.poll() is not None:
                self._on_exit(proc.poll())
                break

    # ------------------------------------------------------------------
    def status_text(self) -> Dict[str, str]:
        return {
            "status": self.status,
            "pid": str(self.pid or "-"),
            "uptime": (
                f"{int(time.time() - self.started_at)}s"
                if self.started_at
                else "-"
            ),
            "players": str(len(self.players)),
        }

    def send_command(self, cmd: str) -> bool:
        if not cmd.strip():
            return True
        if self.proc is None or self.proc.poll() is not None:
            self.log.append("[mcbsadmin] Server is not running — ignoring command.")
            return False
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
            self.log.append(f"[mcbsadmin] > {cmd}")
            return True
        except OSError:
            return False

    def stop(self, graceful: bool = True) -> bool:
        if self.proc is None or self.proc.poll() is not None:
            self._cleanup()
            return True
        self._manual_stop = True
        self.status = "stopping"
        self.on_status.fire(self.status, "Stopping server…")
        if graceful:
            self.send_command("stop")
            deadline = time.time() + 25.0
            while time.time() < deadline:
                proc = self.proc
                if proc is None or proc.poll() is not None:
                    break
                time.sleep(0.2)
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self._force_kill()
        self._cleanup()
        return True

    def _force_kill(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
            except OSError:
                pass

    def _cleanup(self) -> None:
        self._stop_flag.set()
        with self.players_lock:
            self.players.clear()
            self.player_ips.clear()
            self.max_players = None
        self.pid = None
        self.proc = None
        self.started_at = None
        self.status = "stopped"

    def _on_exit(self, _code: int) -> None:
        self._cleanup()
        if not self._manual_stop:
            self.last_message = "Server exited unexpectedly."
            self.on_status.fire(self.status, self.last_message)

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        try:
            self.stop(graceful=True)
        except Exception:
            self._cleanup()
