from pathlib import Path

import pytest

from protopresence.config import ActivityOverride, ConfigError, load_config, set_top_level_scalar


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_minimal_valid_config(tmp_path: Path) -> None:
    path = write(tmp_path, 'client_id = "12345"\n')
    config = load_config(path)
    assert config.client_id == "12345"
    assert config.poll_interval == 15.0
    assert config.extra_steam_roots == []
    assert config.native == {}


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_missing_client_id_raises(tmp_path: Path) -> None:
    path = write(tmp_path, "poll_interval = 5.0\n")
    with pytest.raises(ConfigError, match="client_id"):
        load_config(path)


def test_invalid_poll_interval_raises(tmp_path: Path) -> None:
    path = write(tmp_path, 'client_id = "1"\npoll_interval = -3\n')
    with pytest.raises(ConfigError, match="positive"):
        load_config(path)


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = write(tmp_path, "this is not valid toml [[[")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


def test_bare_string_override_shortcuts_to_details(tmp_path: Path) -> None:
    path = write(tmp_path, 'client_id = "1"\n[native]\nnvim = "Editing code"\n')
    config = load_config(path)
    assert config.native["nvim"] == ActivityOverride(details="Editing code")


def test_bool_true_override_means_watch_with_defaults(tmp_path: Path) -> None:
    path = write(tmp_path, 'client_id = "1"\n[native]\nardour = true\n')
    config = load_config(path)
    assert config.native["ardour"] == ActivityOverride()


def test_table_override_with_all_fields(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        (
            'client_id = "1"\n'
            "[steam_overrides]\n"
            '440 = { details = "Fragging", state = "In a match", large_image = "tf2", '
            'large_text = "TF2", small_image = "class", small_text = "Scout" }\n'
        ),
    )
    config = load_config(path)
    override = config.steam_overrides["440"]
    assert override.details == "Fragging"
    assert override.state == "In a match"
    assert override.large_image == "tf2"
    assert override.large_text == "TF2"
    assert override.small_image == "class"
    assert override.small_text == "Scout"


def test_unknown_override_field_raises(tmp_path: Path) -> None:
    path = write(tmp_path, 'client_id = "1"\n[native]\nfoo = { bogus_field = "x" }\n')
    with pytest.raises(ConfigError, match="unknown override field"):
        load_config(path)


def test_native_keys_lowercased(tmp_path: Path) -> None:
    path = write(tmp_path, 'client_id = "1"\n[native]\nCode = "Writing code"\n')
    config = load_config(path)
    assert "code" in config.native
    assert "Code" not in config.native


def test_steam_override_keys_not_lowercased(tmp_path: Path) -> None:
    # Steam appids are numeric strings; lowercasing is a no-op but this
    # guards against accidentally normalizing them like process names.
    path = write(tmp_path, 'client_id = "1"\n[steam_overrides]\n440 = "Playing"\n')
    config = load_config(path)
    assert config.steam_overrides["440"].details == "Playing"


def test_extra_steam_roots_must_be_string_list(tmp_path: Path) -> None:
    path = write(tmp_path, 'client_id = "1"\nextra_steam_roots = [1, 2]\n')
    with pytest.raises(ConfigError, match="extra_steam_roots"):
        load_config(path)


class TestSetTopLevelScalar:
    def test_inserts_when_missing(self) -> None:
        result = set_top_level_scalar("", "client_id", '"abc"')
        assert result == 'client_id = "abc"\n'

    def test_replaces_existing_top_level_key(self) -> None:
        original = 'client_id = "old"\npoll_interval = 15.0\n'
        result = set_top_level_scalar(original, "client_id", '"new"')
        assert 'client_id = "new"' in result
        assert "poll_interval = 15.0" in result
        assert "old" not in result

    def test_preserves_tables_and_comments(self) -> None:
        original = (
            "# a comment\n"
            'client_id = "old"\n'
            "\n"
            "[native]\n"
            'nvim = "Editing code"\n'
        )
        result = set_top_level_scalar(original, "poll_interval", "20.0")
        assert "# a comment" in result
        assert 'client_id = "old"' in result
        assert "[native]" in result
        assert 'nvim = "Editing code"' in result
        assert "poll_interval = 20.0" in result

    def test_does_not_touch_same_named_key_inside_a_table(self) -> None:
        original = 'client_id = "old"\n\n[native]\nclient_id = "should not change"\n'
        result = set_top_level_scalar(original, "client_id", '"new"')
        lines = result.splitlines()
        assert lines[0] == 'client_id = "new"'
        assert 'client_id = "should not change"' in result
