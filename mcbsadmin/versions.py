"""Bedrock Dedicated Server (BDS) discovery and installation.

Unlike the Java server there is no stable, versioned manifest: the
Bedrock server is a native binary shipped as a zip whose build id changes
with every release. The newest stable Linux build is resolved from
Mojang's official download-links API, falling back to the community
Bedrock-OSS BDS registry when that endpoint is unreachable.

Because Bedrock has no "version selector", every install fetches the
latest build automatically. New builds are unpacked over the existing
server directory without renaming any folders: user data (the world,
server.properties, allowlist, permissions) is kept as-is and only the
server binaries/assets are replaced, so a version update never moves or
renames directories.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Optional, Tuple

from .util import download_with_progress

MOJANG_DOWNLOAD_API = (
    "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
)
BDS_REGISTRY = "https://raw.githubusercontent.com/Bedrock-OSS/BDS-Versions/main"
USER_AGENT = "MCBSAdmin/1.0 (terminal minecraft server manager)"
MARKER_FILE = ".mcbsadmin-version"

# Files kept untouched when a new build is unpacked over an existing
# install. Everything else is replaced by the fresh build.
PRESERVED = frozenset(
    {
        "worlds",
        "server.properties",
        "allowlist.json",
        "whitelist.json",
        "permissions.json",
        "config",
        "development_behavior_packs",
        "development_resource_packs",
    }
)

Progress = Callable[[int, int], None]  # (downloaded, total)

# bedrock-server-1.26.43.1.zip  (also handles preview-style suffixes)
ZIP_VERSION_RE = re.compile(r"bedrock-server-(.+?)\.zip$")


def _read_url(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _version_from_url(url: str) -> str:
    m = ZIP_VERSION_RE.search(url)
    if not m:
        raise ValueError(f"Unrecognized Bedrock download URL: {url}")
    return m.group(1)


def _latest_from_mojang(timeout: float = 30.0) -> Tuple[str, str]:
    """Return (version, url) from Mojang's download-links API."""
    data = json.loads(_read_url(MOJANG_DOWNLOAD_API, timeout))
    for link in data["result"]["links"]:
        if link.get("downloadType") == "serverBedrockLinux":
            url = link["downloadUrl"]
            return _version_from_url(url), url
    raise ValueError("Mojang download API returned no Linux Bedrock server link.")


def _latest_from_registry(timeout: float = 30.0) -> Tuple[str, str]:
    """Return (version, url) from the Bedrock-OSS BDS registry."""
    data = json.loads(_read_url(f"{BDS_REGISTRY}/versions.json", timeout))
    version = data["linux"]["stable"]
    meta = json.loads(_read_url(f"{BDS_REGISTRY}/linux/{version}.json", timeout))
    url = meta.get("download_url")
    if not url:
        raise ValueError("BDS registry returned no download URL.")
    return version, url


def latest_build(timeout: float = 30.0) -> Tuple[str, str]:
    """Return (version, url) for the newest stable Linux Bedrock build."""
    try:
        return _latest_from_mojang(timeout)
    except (urllib.error.URLError, ValueError, KeyError, OSError):
        return _latest_from_registry(timeout)


def download_file(
    url: str,
    dest: str,
    progress: Optional[Progress] = None,
    timeout: float = 120.0,
) -> int:
    """Stream-download a file, reporting progress. Returns bytes written."""
    return download_with_progress(url, dest, progress=progress, timeout=timeout)


def read_installed_version(server_dir: str) -> Optional[str]:
    """Return the recorded Bedrock build id of the installed server."""
    try:
        with open(
            os.path.join(server_dir, MARKER_FILE), "r", encoding="utf-8"
        ) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def write_installed_version(server_dir: str, version_id: str) -> None:
    try:
        with open(
            os.path.join(server_dir, MARKER_FILE), "w", encoding="utf-8"
        ) as fh:
            fh.write(f"{version_id}\n")
    except OSError:
        pass


def _merge_build(src_dir: str, server_dir: str) -> None:
    """Copy a freshly-extracted build over the install directory.

    User data (worlds, config files) is preserved; the build's binaries,
    packs and defaults replace their old counterparts. Nothing is renamed
    and no per-version folders are ever created.
    """
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dst = os.path.join(server_dir, item)
        if item in PRESERVED and os.path.exists(dst):
            continue
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            if os.path.exists(dst):
                os.remove(dst)
            shutil.copy2(src, dst)


def install_bedrock(
    server_dir: str,
    progress: Optional[Progress] = None,
    timeout: float = 300.0,
) -> str:
    """Ensure the newest Bedrock server build is installed.

    Returns the installed build id. Downloads and unpacks whenever the
    ``bedrock_server`` binary is missing or an older build is recorded;
    otherwise it is a no-op. World and config folders keep their names
    across updates.
    """
    os.makedirs(server_dir, exist_ok=True)
    version, url = latest_build()
    installed = read_installed_version(server_dir)
    binary = os.path.join(server_dir, "bedrock_server")
    binary_missing = not (os.path.exists(binary) and os.path.getsize(binary) > 0)
    if not (binary_missing or installed != version):
        return version

    if progress:
        progress(0, 0)
    zip_path = os.path.join(server_dir, ".bedrock-server.zip")
    extract_dir = os.path.join(server_dir, ".bedrock-extract")
    try:
        download_file(url, zip_path, progress=progress, timeout=timeout)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        _merge_build(extract_dir, server_dir)
    finally:
        for path in (zip_path, extract_dir):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    binary = os.path.join(server_dir, "bedrock_server")
    if not os.path.exists(binary):
        raise ValueError(f"Downloaded build '{version}' has no bedrock_server binary.")
    os.chmod(binary, 0o755)
    write_installed_version(server_dir, version)
    return version


def download_async(
    server_dir: str,
    on_progress: Progress,
    on_done: Callable[[bool, Optional[str], str], None],
    timeout: float = 300.0,
) -> threading.Thread:
    """Kick off an install in a background thread."""

    def _run() -> None:
        try:
            v_id = install_bedrock(
                server_dir, progress=on_progress, timeout=timeout
            )
            on_done(True, v_id, "ok")
        except (urllib.error.URLError, ValueError, OSError) as exc:
            on_done(False, None, str(exc))

    t = threading.Thread(target=_run, daemon=True, name="mc-installer")
    t.start()
    return t
