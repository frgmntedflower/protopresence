from pathlib import Path

from protopresence.steam import build_app_index, discover_library_paths, find_appinfo_path, find_steam_roots

APPMANIFEST_TEMPLATE = '''"AppState"
{{
\t"appid"\t\t"{appid}"
\t"Universe"\t\t"1"
\t"name"\t\t"{name}"
\t"StateFlags"\t\t"4"
\t"installdir"\t\t"{installdir}"
}}
'''

LIBRARYFOLDERS_TEMPLATE = '''"libraryfolders"
{{
\t"0"
\t{{
\t\t"path"\t\t"{path}"
\t\t"label"\t\t""
\t\t"apps"
\t\t{{
\t\t\t"440"\t\t"1234567"
\t\t}}
\t}}
}}
'''


def make_steamapps(root: Path, appids: list[tuple[str, str, str]]) -> Path:
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True)
    for appid, name, installdir in appids:
        (steamapps / f"appmanifest_{appid}.acf").write_text(
            APPMANIFEST_TEMPLATE.format(appid=appid, name=name, installdir=installdir)
        )
    return steamapps


def test_find_steam_roots_only_returns_existing_dirs(tmp_path: Path) -> None:
    existing = tmp_path / "steam_root"
    existing.mkdir()
    missing = tmp_path / "does_not_exist"
    roots = find_steam_roots([str(existing), str(missing)])
    assert existing in roots
    assert missing not in roots


def test_discover_library_paths_includes_root_steamapps(tmp_path: Path) -> None:
    root = tmp_path / "steam"
    make_steamapps(root, [("440", "Team Fortress 2", "Team Fortress 2")])
    libraries = discover_library_paths([root])
    assert (root / "steamapps") in libraries


def test_discover_library_paths_follows_libraryfolders_vdf(tmp_path: Path) -> None:
    root = tmp_path / "steam"
    make_steamapps(root, [])
    second_library_root = tmp_path / "second_library"
    make_steamapps(second_library_root, [("730", "Counter-Strike 2", "Counter-Strike 2")])

    (root / "steamapps" / "libraryfolders.vdf").write_text(
        LIBRARYFOLDERS_TEMPLATE.format(path=str(second_library_root))
    )

    libraries = discover_library_paths([root])
    assert (root / "steamapps") in libraries
    assert (second_library_root / "steamapps") in libraries


def test_discover_library_paths_deduplicates(tmp_path: Path) -> None:
    root = tmp_path / "steam"
    make_steamapps(root, [])
    # libraryfolders.vdf pointing back at the same root shouldn't duplicate it.
    (root / "steamapps" / "libraryfolders.vdf").write_text(LIBRARYFOLDERS_TEMPLATE.format(path=str(root)))

    libraries = discover_library_paths([root])
    assert libraries.count(root / "steamapps") == 1


def test_build_app_index(tmp_path: Path) -> None:
    root = tmp_path / "steam"
    steamapps = make_steamapps(
        root,
        [
            ("440", "Team Fortress 2", "Team Fortress 2"),
            ("730", "Counter-Strike 2", "Counter-Strike 2"),
        ],
    )
    index = build_app_index([steamapps])
    assert index["440"].name == "Team Fortress 2"
    assert index["730"].installdir == "Counter-Strike 2"


def test_find_appinfo_path_locates_file(tmp_path: Path) -> None:
    root = tmp_path / "steam"
    (root / "appcache").mkdir(parents=True)
    appinfo = root / "appcache" / "appinfo.vdf"
    appinfo.write_bytes(b"fake")
    assert find_appinfo_path([root]) == appinfo


def test_find_appinfo_path_returns_none_when_absent(tmp_path: Path) -> None:
    root = tmp_path / "steam"
    root.mkdir()
    assert find_appinfo_path([root]) is None


def test_build_app_index_skips_malformed_manifest(tmp_path: Path) -> None:
    root = tmp_path / "steam"
    steamapps = make_steamapps(root, [("440", "Team Fortress 2", "Team Fortress 2")])
    (steamapps / "appmanifest_999.acf").write_text("not valid vdf {{{")
    index = build_app_index([steamapps])
    assert "440" in index
    assert "999" not in index
