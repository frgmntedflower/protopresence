"""Detect what the user is currently doing: a Steam game, a bare Wine game, or
a native watched app.

Detection is priority-ordered and stops at the first match: Steam games
(native or Proton) outrank bare Wine games, which outrank native apps.

Everything here that scans process lists takes plain data (``ProcessInfo``)
rather than talking to ``psutil``/``/proc`` directly, so the priority logic
can be unit tested without mocking the OS.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .config import ActivityOverride, Config
from .steam import SteamAppInfo

logger = logging.getLogger(__name__)

#: Environment variables Steam injects into every game process it launches,
#: whether it's a native Linux binary or a Windows .exe under Proton.
STEAM_APPID_ENV_VARS = ("SteamAppId", "SteamGameId")

#: wine/wine64 come from a plain `wine` invocation; the *-preloader variants
#: are what actually shows up as the process name on modern Wine/Proton.
WINE_PROCESS_NAMES = frozenset({"wine", "wine64", "wine-preloader", "wine64-preloader"})

#: Wine's own helper processes -- never the game itself, so we skip past
#: them when scanning a wine process's argv for the real target .exe.
WINE_IGNORE_EXE_NAMES = frozenset(
    {
        "services.exe",
        "explorer.exe",
        "winedevice.exe",
        "plugplay.exe",
        "svchost.exe",
        "rpcss.exe",
        "conhost.exe",
        "start.exe",
        "wineboot.exe",
        "winemenubuilder.exe",
        "rundll32.exe",
    }
)


class ActivityKind(str, Enum):
    STEAM = "steam"
    WINE = "wine"
    NATIVE = "native"


@dataclass(frozen=True)
class ProcessInfo:
    """A minimal snapshot of a running process, decoupled from psutil."""

    pid: int
    name: str
    cmdline: tuple[str, ...]


@dataclass(frozen=True)
class Activity:
    """One detected activity, with an auto-generated fallback display name."""

    kind: ActivityKind
    key: str
    display_name: str
    pid: int


def activity_signature(activity: Activity | None) -> tuple[ActivityKind, str] | None:
    """A comparable identity for an activity, ignoring PID (relaunching the
    same game shouldn't be treated as a change)."""
    if activity is None:
        return None
    return (activity.kind, activity.key)


def read_environ(pid: int) -> dict[str, str]:
    """Read ``/proc/<pid>/environ`` for a process we own.

    Returns an empty dict if the process is gone, not ours (PermissionError),
    or otherwise unreadable -- this is expected for the majority of PIDs on
    the system and is not an error condition.
    """
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except (PermissionError, FileNotFoundError, ProcessLookupError, OSError) as exc:
        logger.debug("Could not read environ for pid %d: %s", pid, exc)
        return {}
    env: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        key, sep, value = entry.partition(b"=")
        if not sep:
            continue
        env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def detect_steam_activity(
    processes: Iterable[ProcessInfo],
    app_index: Mapping[str, SteamAppInfo],
    environ_reader: Callable[[int], Mapping[str, str]],
) -> Activity | None:
    """Find a running process with a Steam-injected AppID, native or Proton."""
    for proc in processes:
        env = environ_reader(proc.pid)
        appid = None
        for var in STEAM_APPID_ENV_VARS:
            value = env.get(var)
            if value:
                appid = value.strip()
                break
        if not appid:
            continue
        info = app_index.get(appid)
        # Never surface a raw numeric AppID -- if it's not in the manifest/
        # appinfo.vdf-derived index (see daemon.refresh_steam_app_index),
        # fall back to a generic label instead of "App 12345".
        display_name = info.name if info else "Playing a Steam Game"
        return Activity(kind=ActivityKind.STEAM, key=appid, display_name=display_name, pid=proc.pid)
    return None


def _extract_exe_from_cmdline(cmdline: Sequence[str]) -> str | None:
    """Pull the target .exe basename out of a wine process's argv, skipping
    Wine's own bookkeeping helpers."""
    for arg in cmdline:
        base = arg.replace("\\", "/").rsplit("/", 1)[-1]
        if base.lower().endswith(".exe") and base.lower() not in WINE_IGNORE_EXE_NAMES:
            return base
    return None


def detect_wine_activity(processes: Iterable[ProcessInfo]) -> Activity | None:
    """Find a bare Wine game launched outside Steam (Lutris, Bottles, raw `wine`)."""
    for proc in processes:
        if proc.name.lower() not in WINE_PROCESS_NAMES:
            continue
        exe = _extract_exe_from_cmdline(proc.cmdline)
        if not exe:
            continue
        return Activity(kind=ActivityKind.WINE, key=exe.lower(), display_name=exe, pid=proc.pid)
    return None


def detect_native_activity(
    processes: Iterable[ProcessInfo],
    watch_names: Iterable[str],
) -> Activity | None:
    """Find the first running process matching the configured native watch-list."""
    watch_set = watch_names if isinstance(watch_names, (set, frozenset)) else set(watch_names)
    for proc in processes:
        name = proc.name.lower()
        if name in watch_set:
            return Activity(kind=ActivityKind.NATIVE, key=name, display_name=proc.name, pid=proc.pid)
    return None


def detect_activity(
    processes: Sequence[ProcessInfo],
    app_index: Mapping[str, SteamAppInfo],
    watch_names: Iterable[str],
    environ_reader: Callable[[int], Mapping[str, str]] = read_environ,
) -> Activity | None:
    """Detect the current activity, priority-ordered: Steam > Wine > native."""
    return (
        detect_steam_activity(processes, app_index, environ_reader)
        or detect_wine_activity(processes)
        or detect_native_activity(processes, watch_names)
    )


def resolve_override(activity: Activity, config: Config) -> ActivityOverride:
    """Look up the configured override table entry for a detected activity.

    A per-activity ``button_label``/``button_url`` wins if set; otherwise
    the top-level default (if configured) applies to every activity.
    """
    table = {
        ActivityKind.STEAM: config.steam_overrides,
        ActivityKind.WINE: config.wine_overrides,
        ActivityKind.NATIVE: config.native,
    }[activity.kind]
    override = table.get(activity.key, ActivityOverride())
    if override.button_label is None and override.button_url is None and config.button_label and config.button_url:
        override = replace(override, button_label=config.button_label, button_url=config.button_url)
    return override
