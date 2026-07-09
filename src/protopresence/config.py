"""Load and validate ``config.toml``.

Configuration errors are always fatal at startup (fail loudly): a daemon
running with a half-understood config is worse than one that refuses to
start. See ``config.example.toml`` for the full schema with commentary.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_OVERRIDE_FIELDS = (
    "details",
    "state",
    "large_image",
    "large_text",
    "small_image",
    "small_text",
)


class ConfigError(Exception):
    """Raised for any missing/malformed configuration. Always fatal at startup."""


@dataclass(frozen=True)
class ActivityOverride:
    """User-supplied Rich Presence fields for one detected activity.

    Any field left as ``None`` falls back to an auto-generated value (see
    ``detect.resolve_override`` / ``presence.build_presence_payload``).
    """

    details: str | None = None
    state: str | None = None
    large_image: str | None = None
    large_text: str | None = None
    small_image: str | None = None
    small_text: str | None = None


@dataclass(frozen=True)
class Config:
    client_id: str
    poll_interval: float
    extra_steam_roots: list[str] = field(default_factory=list)
    native: dict[str, ActivityOverride] = field(default_factory=dict)
    steam_overrides: dict[str, ActivityOverride] = field(default_factory=dict)
    wine_overrides: dict[str, ActivityOverride] = field(default_factory=dict)


def _parse_override(raw: Any) -> ActivityOverride:
    """Parse one override value: a bare string shortcuts to ``details``."""
    if isinstance(raw, str):
        return ActivityOverride(details=raw)
    if isinstance(raw, bool):
        # `key = true` -- watch/track it, no custom fields.
        return ActivityOverride()
    if isinstance(raw, dict):
        unknown = set(raw) - set(_OVERRIDE_FIELDS)
        if unknown:
            raise ConfigError(f"unknown override field(s): {sorted(unknown)}")
        for name, value in raw.items():
            if value is not None and not isinstance(value, str):
                raise ConfigError(f"override field {name!r} must be a string")
        return ActivityOverride(**{name: raw.get(name) for name in _OVERRIDE_FIELDS})
    raise ConfigError(f"invalid override value: {raw!r} (expected string, bool, or table)")


def _parse_override_table(raw: Any, table_name: str, *, lowercase_keys: bool) -> dict[str, ActivityOverride]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"[{table_name}] must be a table")
    result: dict[str, ActivityOverride] = {}
    for key, value in raw.items():
        try:
            override = _parse_override(value)
        except ConfigError as exc:
            raise ConfigError(f"[{table_name}].{key}: {exc}") from exc
        result[key.lower() if lowercase_keys else key] = override
    return result


def load_config(path: Path) -> Config:
    """Load and validate a config.toml file, raising ``ConfigError`` on any problem."""
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path}\n"
            "Copy config.example.toml there and fill in your client_id."
        )
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc

    client_id = data.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ConfigError(
            "client_id is required and must be a non-empty string "
            "(create an application at https://discord.com/developers/applications)"
        )

    poll_interval = data.get("poll_interval", 15.0)
    if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)):
        raise ConfigError("poll_interval must be a number")
    if poll_interval <= 0:
        raise ConfigError("poll_interval must be positive")

    extra_steam_roots = data.get("extra_steam_roots", [])
    if not isinstance(extra_steam_roots, list) or not all(isinstance(p, str) for p in extra_steam_roots):
        raise ConfigError("extra_steam_roots must be a list of strings")

    native = _parse_override_table(data.get("native"), "native", lowercase_keys=True)
    steam_overrides = _parse_override_table(data.get("steam_overrides"), "steam_overrides", lowercase_keys=False)
    wine_overrides = _parse_override_table(data.get("wine_overrides"), "wine_overrides", lowercase_keys=True)

    return Config(
        client_id=client_id,
        poll_interval=float(poll_interval),
        extra_steam_roots=extra_steam_roots,
        native=native,
        steam_overrides=steam_overrides,
        wine_overrides=wine_overrides,
    )


def set_top_level_scalar(text: str, key: str, literal_value: str) -> str:
    """Replace or insert a single top-level ``key = value`` line in raw TOML text.

    Used by the GUI's "Save" button to update ``client_id``/``poll_interval``
    in place without a full TOML writer -- comments and override tables
    ([native], [steam_overrides], [wine_overrides]) are left untouched.
    Only lines *before* the first ``[table]`` header are considered, so this
    can never mistake a same-named key nested inside a table for the
    top-level one.
    """
    lines = text.splitlines(keepends=True)
    first_table_idx = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"^\s*\[", line):
            first_table_idx = i
            break

    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$")
    new_line = f"{key} = {literal_value}\n"
    for i in range(first_table_idx):
        if pattern.match(lines[i]):
            lines[i] = new_line
            return "".join(lines)

    lines.insert(first_table_idx, new_line)
    return "".join(lines)
