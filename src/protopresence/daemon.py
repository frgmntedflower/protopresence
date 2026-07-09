"""Main poll loop and CLI entrypoint for the ``protopresence`` command."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil

from .appinfo import extract_names, parse_appinfo
from .config import Config, ConfigError, load_config
from .detect import Activity, ProcessInfo, activity_signature, detect_activity, read_environ, resolve_override
from .presence import DiscordPresenceManager, build_presence_payload
from .steam import SteamAppInfo, build_app_index, discover_library_paths, find_appinfo_path, find_steam_roots

logger = logging.getLogger("protopresence")

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "protopresence" / "config.toml"

#: How often (seconds) to re-scan Steam libraries for newly installed/removed
#: games, independent of the (usually much shorter) process poll interval.
STEAM_INDEX_REFRESH_INTERVAL = 300.0


@dataclass(frozen=True)
class PollStatus:
    """A snapshot of one poll tick, handed to `run()`'s optional status callback.

    This exists so a frontend (e.g. the GUI) can observe live state without
    reaching into the poll loop's internals.
    """

    activity: Activity | None
    discord_connected: bool
    checked_at: float


def list_processes() -> list[ProcessInfo]:
    """Snapshot every running process's pid/name/cmdline via psutil."""
    processes: list[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        processes.append(
            ProcessInfo(
                pid=info["pid"],
                name=info["name"] or "",
                cmdline=tuple(info["cmdline"] or ()),
            )
        )
    return processes


def refresh_steam_app_index(config: Config) -> dict[str, SteamAppInfo]:
    """Rebuild the AppID -> game-name index from all discovered Steam libraries.

    Installed games (with an ``appmanifest_*.acf``) always take priority.
    Steam's local ``appinfo.vdf`` client cache fills in a name for any
    AppID that's missing one -- e.g. a game Steam launched but that isn't
    tracked under a library path we recognize -- so a real title is shown
    instead of a bare numeric AppID whenever Steam's local cache knows it.
    """
    roots = find_steam_roots(config.extra_steam_roots)
    if not roots:
        logger.debug("No Steam install roots found")
    libraries = discover_library_paths(roots)
    index = build_app_index(libraries)
    logger.debug("Indexed %d installed Steam app(s) across %d librar(y/ies)", len(index), len(libraries))

    appinfo_path = find_appinfo_path(roots)
    if appinfo_path is not None:
        names = extract_names(parse_appinfo(appinfo_path))
        added = 0
        for appid, name in names.items():
            if appid not in index:
                index[appid] = SteamAppInfo(appid=appid, name=name, installdir="")
                added += 1
        logger.debug("appinfo.vdf added %d name(s) not covered by an install manifest", added)

    return index


def run(
    config: Config,
    *,
    once: bool = False,
    stop_event: threading.Event | None = None,
    on_status: Callable[[PollStatus], None] | None = None,
) -> None:
    """Run the poll loop until ``stop_event`` is set (or once, if requested).

    If given, ``on_status`` is called after every poll tick (whether or not
    the activity changed) with a :class:`PollStatus` snapshot -- this is how
    the GUI stays up to date without polling the daemon itself.
    """
    stop_event = stop_event or threading.Event()
    presence = DiscordPresenceManager(config.client_id)
    watch_names = set(config.native.keys())

    app_index = refresh_steam_app_index(config)
    last_index_refresh = time.monotonic()

    # Sentinel distinct from `None` (which means "no activity") so the very
    # first tick always triggers an update/clear call.
    last_signature: object = object()

    try:
        while not stop_event.is_set():
            now = time.monotonic()
            if now - last_index_refresh >= STEAM_INDEX_REFRESH_INTERVAL:
                app_index = refresh_steam_app_index(config)
                last_index_refresh = now

            processes = list_processes()
            activity = detect_activity(processes, app_index, watch_names, read_environ)
            signature = activity_signature(activity)

            if signature != last_signature:
                if activity is None:
                    logger.info("No tracked activity detected; clearing presence")
                    presence.clear()
                else:
                    override = resolve_override(activity, config)
                    payload = build_presence_payload(activity, override)
                    logger.info(
                        "Activity changed -> %s:%s (%s)",
                        activity.kind.value,
                        activity.key,
                        payload["details"],
                    )
                    presence.update(payload, is_new_activity=True)
                last_signature = signature

            if on_status is not None:
                on_status(PollStatus(activity=activity, discord_connected=presence.connected, checked_at=time.time()))

            if once:
                return
            stop_event.wait(config.poll_interval)
    finally:
        presence.clear()
        presence.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="protopresence",
        description=(
            "Set Discord Rich Presence automatically based on running Steam/Proton/Wine "
            "games and configured native apps."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config.toml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single detection cycle and exit (useful for debugging detection)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    logger.info("protopresence starting (poll interval: %.1fs, config: %s)", config.poll_interval, args.config)

    stop_event = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("Received signal %d, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        run(config, once=args.once, stop_event=stop_event)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
