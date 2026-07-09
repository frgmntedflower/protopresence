"""Parse Steam's local ``appcache/appinfo.vdf`` -- the client's cache of
metadata (name, type, ...) for every app it has ever seen, whether or not
it's installed.

``appmanifest_<id>.acf`` (see ``steam.py``) only exists for games actually
installed on this machine. ``appinfo.vdf`` is a much larger local database
Steam maintains for anything it knows about -- owned games, games merely
viewed on the store, demos, tools -- which lets us resolve a game's real
name purely from local data even when nothing is installed under a
recognized Steam library path (e.g. a game symlinked or moved outside
Steam's own bookkeeping).

This is Steam's internal binary "KeyValues" cache format. It isn't
officially documented by Valve, but it's stable and widely relied on by
community tools (SteamKit and friends). The layout, reverse-engineered and
confirmed against a real cache file:

* Header: ``uint32`` magic, ``uint32`` universe (always 1).
* If magic is the newer, string-table-based format (``0x07564429``): a
  ``int64`` immediately follows giving the byte offset of a string table
  used for *key* names (not values -- see below). The table itself is
  ``uint32`` count followed by that many null-terminated strings, appended
  after the last app entry.
* A sequence of app entries, terminated by an AppID of 0. Each entry has a
  ``uint32`` AppID, a ``uint32`` size (of everything from the next field to
  the end of the entry, letting a reader skip entries it can't parse),
  fixed bookkeeping fields (info state, last-updated, PICS token, one or
  two SHA1 hashes depending on magic), then a binary KeyValues blob.
* Binary KeyValues: a byte type tag, then a key, then a value (for object
  types, the "value" is itself a nested sequence of type/key/value entries
  terminated by an End tag). Key names use the string table when one is
  present; string *values* are always inline null-terminated text in every
  version we've seen, even when a string table exists -- likely because
  key names repeat constantly across thousands of apps while value text
  mostly doesn't, so only keys are worth deduplicating.

We only care about ``<appid>.common.name``, so unsupported/exotic value
types abort parsing that one entry (not the whole file); any failure here
is non-fatal to the daemon -- this is a "nice to have" name enrichment
layer, never load-bearing for detection itself.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

_MAGIC_NO_STRING_TABLE = (0x07564427, 0x07564428)
_MAGIC_STRING_TABLE = 0x07564429
_MAGIC_EXTRA_HASH_FROM = 0x07564428

_TYPE_OBJECT = 0x00
_TYPE_STRING = 0x01
_TYPE_INT32 = 0x02
_TYPE_FLOAT32 = 0x03
_TYPE_POINTER = 0x04
_TYPE_COLOR = 0x06
_TYPE_UINT64 = 0x07
_TYPE_END = 0x08
_TYPE_INT64 = 0x0A


class _Reader:
    """A tiny cursor over the file's bytes; raises IndexError/struct.error on
    running off the end, which callers turn into "give up on this file"."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def i64(self) -> int:
        value = struct.unpack_from("<q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def u64(self) -> int:
        value = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def skip(self, n: int) -> None:
        self.pos += n

    def cstring(self) -> str:
        end = self.data.index(b"\x00", self.pos)
        value = self.data[self.pos : end].decode("utf-8", "replace")
        self.pos = end + 1
        return value


def _parse_object(reader: _Reader, string_table: list[str] | None) -> dict:
    obj: dict = {}
    while True:
        type_id = reader.u8()
        if type_id == _TYPE_END:
            return obj
        key = string_table[reader.u32()] if string_table is not None else reader.cstring()
        if type_id == _TYPE_OBJECT:
            obj[key] = _parse_object(reader, string_table)
        elif type_id == _TYPE_STRING:
            obj[key] = reader.cstring()
        elif type_id in (_TYPE_INT32, _TYPE_FLOAT32, _TYPE_POINTER, _TYPE_COLOR):
            obj[key] = reader.u32()
        elif type_id in (_TYPE_UINT64, _TYPE_INT64):
            obj[key] = reader.u64()
        else:
            raise ValueError(f"unsupported appinfo value type 0x{type_id:02x}")


def _read_string_table(data: bytes, offset: int) -> list[str]:
    reader = _Reader(data, offset)
    count = reader.u32()
    return [reader.cstring() for _ in range(count)]


def parse_appinfo(path: Path) -> dict[str, dict]:
    """Parse ``appinfo.vdf``, returning ``{appid: <that app's KeyValues dict>}``.

    Returns an empty dict (logging at debug level) on any failure -- unknown
    magic, truncated file, an app entry using a value type we don't
    understand. This cache is purely best-effort enrichment, so a parse
    failure should never propagate up and affect detection.
    """
    try:
        data = path.read_bytes()
        reader = _Reader(data)
        magic = reader.u32()
        reader.u32()  # universe; always 1, not otherwise meaningful here

        string_table: list[str] | None = None
        if magic == _MAGIC_STRING_TABLE:
            table_offset = reader.i64()
            string_table = _read_string_table(data, table_offset)
        elif magic not in _MAGIC_NO_STRING_TABLE:
            logger.debug("Unrecognized appinfo.vdf magic 0x%08x in %s", magic, path)
            return {}

        apps: dict[str, dict] = {}
        while True:
            appid = reader.u32()
            if appid == 0:
                break
            size = reader.u32()
            entry_end = reader.pos + size
            reader.skip(4 + 4 + 8 + 20)  # info_state, last_updated, pics_token, sha1(text)
            reader.u32()  # change_number
            if magic >= _MAGIC_EXTRA_HASH_FROM:
                reader.skip(20)  # sha1(binary data)
            try:
                entry = _parse_object(reader, string_table)
            except (ValueError, IndexError, struct.error) as exc:
                logger.debug("Skipping unparsable appinfo entry for appid %d: %s", appid, exc)
                entry = {}
            apps[str(appid)] = entry.get("appinfo", entry)
            reader.pos = entry_end
        return apps
    except (OSError, IndexError, struct.error) as exc:
        logger.debug("Failed to parse %s: %s", path, exc)
        return {}


def extract_names(apps: dict[str, dict]) -> dict[str, str]:
    """Pull ``common.name`` out of every parsed app entry that has one."""
    names: dict[str, str] = {}
    for appid, entry in apps.items():
        common = entry.get("common")
        if isinstance(common, dict):
            name = common.get("name")
            if isinstance(name, str) and name:
                names[appid] = name
    return names
