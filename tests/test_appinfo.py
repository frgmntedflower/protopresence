"""Tests for the appinfo.vdf binary parser, built against hand-constructed
byte streams rather than a real Steam install -- see the module docstring
in appinfo.py for the reverse-engineered format this mirrors.
"""

from __future__ import annotations

import struct
from pathlib import Path

from protopresence.appinfo import extract_names, parse_appinfo

MAGIC_NO_TABLE = 0x07564427
MAGIC_WITH_HASH = 0x07564428
MAGIC_WITH_TABLE = 0x07564429

_TYPE_OBJECT = 0x00
_TYPE_STRING = 0x01
_TYPE_INT32 = 0x02
_TYPE_END = 0x08


def _fixed_entry_header(change_number: int = 1) -> bytes:
    info_state = struct.pack("<I", 1)
    last_updated = struct.pack("<I", 0)
    pics_token = struct.pack("<Q", 0)
    text_hash = b"\x00" * 20
    change = struct.pack("<I", change_number)
    return info_state + last_updated + pics_token + text_hash + change


def _kv_entry_inline(appid_value: int, name: str) -> bytes:
    """Encode {"appinfo": {"appid": <int32>, "common": {"name": <string>}}}
    with inline (null-terminated) keys, matching the pre-string-table format.
    """
    out = bytearray()
    out += bytes([_TYPE_OBJECT]) + b"appinfo\x00"
    out += bytes([_TYPE_INT32]) + b"appid\x00" + struct.pack("<I", appid_value)
    out += bytes([_TYPE_OBJECT]) + b"common\x00"
    out += bytes([_TYPE_STRING]) + b"name\x00" + name.encode("utf-8") + b"\x00"
    out += bytes([_TYPE_END])  # closes "common"
    out += bytes([_TYPE_END])  # closes "appinfo"
    out += bytes([_TYPE_END])  # closes the entry's implicit root object
    return bytes(out)


def _kv_entry_with_table(appid_value: int, name: str, key_index: dict[str, int]) -> bytes:
    """Same shape as `_kv_entry_inline`, but keys are string-table indices
    (values stay inline -- matches what we verified against a real cache).
    """

    def key(k: str) -> bytes:
        return struct.pack("<I", key_index[k])

    out = bytearray()
    out += bytes([_TYPE_OBJECT]) + key("appinfo")
    out += bytes([_TYPE_INT32]) + key("appid") + struct.pack("<I", appid_value)
    out += bytes([_TYPE_OBJECT]) + key("common")
    out += bytes([_TYPE_STRING]) + key("name") + name.encode("utf-8") + b"\x00"
    out += bytes([_TYPE_END])
    out += bytes([_TYPE_END])
    out += bytes([_TYPE_END])
    return bytes(out)


def _build_entry(magic: int, appid: int, kv_blob: bytes) -> bytes:
    fixed = _fixed_entry_header()
    if magic >= MAGIC_WITH_HASH:
        fixed += b"\x00" * 20
    body = fixed + kv_blob
    return struct.pack("<II", appid, len(body)) + body


def _build_file_no_table(magic: int, games: dict[int, str]) -> bytes:
    out = struct.pack("<II", magic, 1)
    for appid, name in games.items():
        out += _build_entry(magic, appid, _kv_entry_inline(appid, name))
    out += struct.pack("<I", 0)  # terminator
    return out


def _build_file_with_table(games: dict[int, str]) -> bytes:
    keys = ["appinfo", "appid", "common", "name"]
    key_index = {k: i for i, k in enumerate(keys)}

    header = struct.pack("<II", MAGIC_WITH_TABLE, 1)
    header += b"\x00" * 8  # placeholder for the table offset, patched below

    body = b""
    for appid, name in games.items():
        body += _build_entry(MAGIC_WITH_TABLE, appid, _kv_entry_with_table(appid, name, key_index))
    body += struct.pack("<I", 0)  # terminator

    table_offset = len(header) + len(body)
    header = struct.pack("<II", MAGIC_WITH_TABLE, 1) + struct.pack("<q", table_offset)

    table = struct.pack("<I", len(keys))
    for k in keys:
        table += k.encode("utf-8") + b"\x00"

    return header + body + table


def test_parse_appinfo_no_string_table(tmp_path: Path) -> None:
    path = tmp_path / "appinfo.vdf"
    path.write_bytes(_build_file_no_table(MAGIC_NO_TABLE, {440: "Team Fortress 2", 730: "Counter-Strike 2"}))
    apps = parse_appinfo(path)
    assert extract_names(apps) == {"440": "Team Fortress 2", "730": "Counter-Strike 2"}


def test_parse_appinfo_with_extra_hash_field(tmp_path: Path) -> None:
    path = tmp_path / "appinfo.vdf"
    path.write_bytes(_build_file_no_table(MAGIC_WITH_HASH, {440: "Team Fortress 2"}))
    apps = parse_appinfo(path)
    assert extract_names(apps) == {"440": "Team Fortress 2"}


def test_parse_appinfo_with_string_table(tmp_path: Path) -> None:
    path = tmp_path / "appinfo.vdf"
    path.write_bytes(_build_file_with_table({440: "Team Fortress 2", 3291080: "DesperaDrops"}))
    apps = parse_appinfo(path)
    assert extract_names(apps) == {"440": "Team Fortress 2", "3291080": "DesperaDrops"}


def test_parse_appinfo_missing_file_returns_empty(tmp_path: Path) -> None:
    assert parse_appinfo(tmp_path / "does_not_exist.vdf") == {}


def test_parse_appinfo_unknown_magic_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "appinfo.vdf"
    path.write_bytes(struct.pack("<II", 0xDEADBEEF, 1) + struct.pack("<I", 0))
    assert parse_appinfo(path) == {}


def test_parse_appinfo_truncated_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "appinfo.vdf"
    full = _build_file_no_table(MAGIC_NO_TABLE, {440: "Team Fortress 2"})
    path.write_bytes(full[: len(full) // 2])
    assert parse_appinfo(path) == {}


def test_extract_names_skips_entries_without_common_name() -> None:
    apps = {"1": {"common": {}}, "2": {}, "3": {"common": {"name": "Real Game"}}}
    assert extract_names(apps) == {"3": "Real Game"}
