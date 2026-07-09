"""A minimal parser for Valve's KeyValues ("VDF") text format.

Steam uses this format for ``libraryfolders.vdf`` and per-game
``appmanifest_<id>.acf`` files. We only need to read a handful of scalar and
nested-object values, so a full grammar (arrays, includes, conditionals,
macros) is unnecessary -- this handles the quoted-string-and-braces subset
that Steam actually emits for these files.
"""

from __future__ import annotations

import re

#: Matches either a double-quoted string (with backslash escapes) or a
#: brace. Unquoted bareword tokens do not appear in the files we parse.
_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def _tokenize(text: str) -> list[str]:
    """Split VDF text into a flat token stream, dropping ``//`` comment lines."""
    tokens: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        for match in _TOKEN_RE.finditer(line):
            brace = match.group(2)
            if brace:
                tokens.append(brace)
            else:
                value = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
                tokens.append(value)
    return tokens


def parse_vdf(text: str) -> dict:
    """Parse VDF/KeyValues text into a nested ``dict``.

    Each object becomes a ``dict``; scalar values are left as strings.
    Malformed input raises :class:`ValueError`.
    """
    tokens = _tokenize(text)
    pos = 0

    def parse_object() -> dict:
        nonlocal pos
        obj: dict = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return obj
            if tok == "{":
                raise ValueError("unexpected '{' where a key was expected")
            key = tok
            pos += 1
            if pos >= len(tokens):
                raise ValueError(f"truncated VDF: no value for key {key!r}")
            nxt = tokens[pos]
            if nxt == "{":
                pos += 1
                obj[key] = parse_object()
            elif nxt == "}":
                raise ValueError(f"unexpected '}}' where a value was expected for key {key!r}")
            else:
                obj[key] = nxt
                pos += 1
        return obj

    return parse_object()
