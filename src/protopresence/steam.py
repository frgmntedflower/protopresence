"""Discover installed Steam libraries and index their app manifests.

This never talks to the network or the Steam Web API -- everything here is
read from local files Steam already maintains on disk:

* ``libraryfolders.vdf`` lists every Steam Library folder (the default one
  plus any the user added on other drives).
* Each library's ``steamapps/appmanifest_<appid>.acf`` describes one
  installed app: its numeric AppID, display name, and install directory.

We use this purely to turn a ``SteamAppId`` seen in a running process's
environment into a human-readable game name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .vdf import parse_vdf

logger = logging.getLogger(__name__)

#: Default locations Steam is known to install itself to on Linux.
_DEFAULT_STEAM_ROOTS = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak
)


@dataclass(frozen=True)
class SteamAppInfo:
    """Metadata about one locally installed Steam app, from its manifest."""

    appid: str
    name: str
    installdir: str


def find_steam_roots(extra_roots: Iterable[str] = ()) -> list[Path]:
    """Return existing Steam install roots: defaults plus any configured extras."""
    candidates = [Path(p).expanduser() for p in _DEFAULT_STEAM_ROOTS]
    candidates.extend(Path(p).expanduser() for p in extra_roots)
    seen: set[Path] = set()
    roots: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def find_appinfo_path(roots: Iterable[Path]) -> Path | None:
    """Locate the ``appcache/appinfo.vdf`` client cache under a Steam install root.

    Unlike library folders, this is a single per-installation file (Steam's
    local database of every app it has ever seen, not just installed ones),
    so we just return the first one found.
    """
    for root in roots:
        candidate = root / "appcache" / "appinfo.vdf"
        if candidate.is_file():
            return candidate
    return None


def discover_library_paths(roots: Iterable[Path]) -> list[Path]:
    """Expand Steam install roots into every ``steamapps`` library folder.

    Each root's own ``steamapps`` directory is included, plus any additional
    libraries listed in that root's ``libraryfolders.vdf`` (e.g. a second
    Steam Library on another disk).
    """
    seen: set[Path] = set()
    libraries: list[Path] = []

    def add(steamapps: Path) -> None:
        resolved = steamapps.resolve() if steamapps.exists() else steamapps
        if steamapps.is_dir() and resolved not in seen:
            seen.add(resolved)
            libraries.append(steamapps)

    for root in roots:
        steamapps = root / "steamapps"
        add(steamapps)

        library_folders_vdf = steamapps / "libraryfolders.vdf"
        if not library_folders_vdf.is_file():
            continue
        try:
            data = parse_vdf(library_folders_vdf.read_text(errors="replace"))
        except (OSError, ValueError) as exc:
            logger.debug("Failed to parse %s: %s", library_folders_vdf, exc)
            continue

        folders = data.get("libraryfolders")
        if not isinstance(folders, dict):
            continue
        for entry in folders.values():
            if not isinstance(entry, dict):
                continue
            path_str = entry.get("path")
            if not path_str:
                continue
            add(Path(path_str) / "steamapps")

    return libraries


def build_app_index(library_paths: Iterable[Path]) -> dict[str, SteamAppInfo]:
    """Parse every ``appmanifest_*.acf`` under the given libraries into an index by AppID."""
    index: dict[str, SteamAppInfo] = {}
    for steamapps in library_paths:
        try:
            manifests = sorted(steamapps.glob("appmanifest_*.acf"))
        except OSError as exc:
            logger.debug("Failed to list %s: %s", steamapps, exc)
            continue
        for manifest in manifests:
            try:
                data = parse_vdf(manifest.read_text(errors="replace"))
            except (OSError, ValueError) as exc:
                logger.debug("Failed to parse %s: %s", manifest, exc)
                continue
            state = data.get("AppState")
            if not isinstance(state, dict):
                continue
            appid = state.get("appid")
            name = state.get("name")
            if not appid or not name:
                continue
            index[str(appid)] = SteamAppInfo(
                appid=str(appid),
                name=name,
                installdir=state.get("installdir", ""),
            )
    return index
