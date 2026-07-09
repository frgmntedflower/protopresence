"""A reconnect-safe wrapper around Discord's local IPC socket via ``pypresence``.

Discord may not be running yet, may be closed mid-session, or its IPC pipe
may throw on almost any call. None of that should ever crash the daemon --
we log and retry connecting on the next poll tick instead.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pypresence import Presence

from .config import ActivityOverride
from .detect import Activity, ActivityKind

logger = logging.getLogger(__name__)

#: Steam's public per-AppID store header image. This is a predictable CDN
#: URL, not a network call protopresence itself makes -- we just hand the
#: URL to Discord, and Discord's own client fetches it, the same way it
#: would fetch any other external image URL passed as an activity asset.
_STEAM_HEADER_IMAGE_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"


def build_presence_payload(activity: Activity, override: ActivityOverride) -> dict[str, str]:
    """Merge a detected activity with its config override into pypresence kwargs.

    Any override field left unset falls back to the activity's
    auto-generated display name (for ``details``), or -- for Steam games --
    to that game's store header image (for ``large_image``/``large_text``),
    or is simply omitted.
    """
    payload: dict[str, str] = {"details": override.details or activity.display_name}

    large_image = override.large_image
    large_text = override.large_text
    if activity.kind == ActivityKind.STEAM:
        # The header image only needs the AppID, so it's available even
        # when the game's name itself couldn't be resolved locally.
        large_image = large_image or _STEAM_HEADER_IMAGE_URL.format(appid=activity.key)
        large_text = large_text or activity.display_name

    optional_fields = {
        "state": override.state,
        "large_image": large_image,
        "large_text": large_text,
        "small_image": override.small_image,
        "small_text": override.small_text,
    }
    for field_name, value in optional_fields.items():
        if value:
            payload[field_name] = value
    return payload


class DiscordPresenceManager:
    """Owns the pypresence client and absorbs every failure mode it can throw.

    Callers just call ``update()``/``clear()`` every poll tick; connection
    state and reconnection are handled internally.
    """

    def __init__(self, client_id: str) -> None:
        self._client_id = client_id
        self._presence: Presence | None = None
        self._connected = False
        self._activity_start: int | None = None

    def _connect(self) -> bool:
        try:
            presence = Presence(self._client_id)
            presence.connect()
        except Exception as exc:  # pypresence's failure modes are not a stable, narrow set
            logger.debug("Discord IPC connect failed (is Discord running?): %s", exc)
            self._presence = None
            self._connected = False
            return False
        self._presence = presence
        self._connected = True
        logger.info("Connected to Discord IPC")
        return True

    def _ensure_connected(self) -> bool:
        return self._connected or self._connect()

    def update(self, payload: dict[str, Any], *, is_new_activity: bool) -> None:
        """Push a Rich Presence update. Never raises; failures are logged and retried later."""
        if not self._ensure_connected():
            return
        if is_new_activity or self._activity_start is None:
            self._activity_start = int(time.time())
        assert self._presence is not None
        try:
            self._presence.update(start=self._activity_start, **payload)
        except Exception as exc:
            logger.warning("Discord update failed, will reconnect and retry next tick: %s", exc)
            self._connected = False
            self._presence = None

    def clear(self) -> None:
        """Clear the current Rich Presence, if connected. Never raises."""
        self._activity_start = None
        if not self._connected or self._presence is None:
            return
        try:
            self._presence.clear()
        except Exception as exc:
            logger.debug("Discord clear failed (probably already disconnected): %s", exc)
            self._connected = False
            self._presence = None

    @property
    def connected(self) -> bool:
        """Whether the last connection attempt to Discord's IPC socket succeeded."""
        return self._connected

    def close(self) -> None:
        """Release the IPC connection at shutdown. Never raises."""
        if self._presence is not None:
            try:
                self._presence.close()
            except Exception as exc:
                logger.debug("Discord close failed: %s", exc)
        self._presence = None
        self._connected = False
