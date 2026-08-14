"""Configuration management for MCBSAdmin.

Everything lives in a single config file under a platform-appropriate
location (XDG_CONFIG_HOME on Linux, which also keeps uploaded saves and
server downloads together so the whole thing is portable via a single
directory).

The data directory is deliberately user-owned: it defaults to
``$XDG_CONFIG_HOME/mcbsadmin`` (or ``~/.config/mcbsadmin``) and never to
anything derived from the install prefix (``/usr``), the Python module
location or the current working directory. When the package is installed
system-wide (``/usr/bin`` + ``/usr/share``) the app therefore stores its
state under the user's home directory instead of trying to write into the
(read-only) install tree. ``MCBSADMIN_DATA_DIR`` overrides the base for
both the config file and the default server directory, and ``--data-dir``
on the CLI sets it explicitly.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any, Dict, Optional

DEFAULTS: Dict[str, Any] = {
    "server_dir": None,          # directory holding bedrock_server, worlds, etc.
    "version": None,             # installed Bedrock build id
    "level": "level",            # active world (level-name in server.properties)
    "world": {},                 # server.properties overrides (World Options)
    "gameport": 19132,           # Bedrock server default port (UDP)
    "gameportv6": 19133,         # Bedrock server default IPv6 port (UDP)
    "motd": "MCBSAdmin managed server",
}


def default_config_dir() -> str:
    """Return the directory where MCBSAdmin stores its state.

    Resolution order:
      1. ``MCBSADMIN_DATA_DIR`` (explicit override; must be absolute)
      2. ``$XDG_CONFIG_HOME/mcbsadmin`` (absolute)
      3. ``~/.config/mcbsadmin``
      4. a subdir of the system temp dir as a last resort

    The result is always an absolute path. Crucially, an unset/missing
    ``HOME`` or a non-absolute ``XDG_CONFIG_HOME`` must never make the app
    fall back to a relative path such as ``~/.config`` — that would create
    a literal ``~`` folder in the current working directory (which for a
    system install can be ``/usr/bin`` or ``/usr/share``).
    """
    override = os.environ.get("MCBSADMIN_DATA_DIR")
    if override and os.path.isabs(override):
        return os.path.abspath(override)

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and os.path.isabs(xdg):
        return os.path.abspath(os.path.join(xdg, "mcbsadmin"))

    home = os.path.expanduser("~")
    if home in ("", "~") or not os.path.isabs(home):
        home = os.environ.get("HOME")
    if home and os.path.isabs(home):
        return os.path.join(home, ".config", "mcbsadmin")

    return os.path.join(tempfile.gettempdir(), "mcbsadmin")


class Config:
    """Load/save the JSON config file."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = (
            path
            or os.environ.get("MCBSADMIN_CONFIG")
            or os.path.join(default_config_dir(), "config.json")
        )
        # deep-copy: the nested world dict must never be shared between
        # Config instances, or in-place edits would leak back into the
        # module DEFAULTS.
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    for key in DEFAULTS:
                        if key in loaded:
                            self.data[key] = loaded[key]
            except (ValueError, OSError):
                pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def server_dir(self) -> str:
        d = self.data.get("server_dir")
        if d:
            return os.path.expanduser(d)
        sv = os.path.join(default_config_dir(), "server")
        self.data["server_dir"] = sv
        self.save()
        return sv