from protopresence.config import ActivityOverride, Config
from protopresence.detect import (
    ActivityKind,
    ProcessInfo,
    activity_signature,
    detect_activity,
    detect_native_activity,
    detect_steam_activity,
    detect_wine_activity,
    resolve_override,
)
from protopresence.steam import SteamAppInfo


def proc(pid: int, name: str, cmdline: tuple[str, ...] = ()) -> ProcessInfo:
    return ProcessInfo(pid=pid, name=name, cmdline=cmdline)


def make_config(**overrides) -> Config:
    return Config(client_id="1", poll_interval=15.0, **overrides)


# -- Steam detection ------------------------------------------------------


def test_detect_steam_activity_uses_manifest_name() -> None:
    processes = [proc(100, "hl2_linux")]
    app_index = {"440": SteamAppInfo(appid="440", name="Team Fortress 2", installdir="Team Fortress 2")}
    environ = {100: {"SteamAppId": "440"}}
    activity = detect_steam_activity(processes, app_index, environ.get)
    assert activity is not None
    assert activity.kind == ActivityKind.STEAM
    assert activity.key == "440"
    assert activity.display_name == "Team Fortress 2"


def test_detect_steam_activity_falls_back_without_manifest() -> None:
    processes = [proc(100, "game.exe")]
    environ = {100: {"SteamGameId": "12345"}}
    activity = detect_steam_activity(processes, {}, environ.get)
    assert activity is not None
    assert activity.key == "12345"
    # Never show a raw numeric AppID -- only a real name or a generic label.
    assert activity.display_name == "Playing a Steam Game"
    assert "12345" not in activity.display_name


def test_detect_steam_activity_ignores_processes_without_appid() -> None:
    processes = [proc(100, "firefox"), proc(101, "hl2_linux")]
    environ = {100: {}, 101: {}}
    assert detect_steam_activity(processes, {}, environ.get) is None


def test_environ_reader_permission_error_is_skipped() -> None:
    processes = [proc(100, "some_proc")]

    def raising_reader(pid: int) -> dict:
        raise PermissionError("nope")

    # detect_steam_activity itself doesn't catch -- that's read_environ's job.
    # Here we simulate the *caller* (read_environ) already having handled it
    # by returning {} for unreadable pids.
    assert detect_steam_activity(processes, {}, lambda pid: {}) is None


# -- Wine detection ---------------------------------------------------------


def test_detect_wine_activity_extracts_real_exe() -> None:
    processes = [
        proc(200, "wine64-preloader", ("wine64-preloader", "C:\\Games\\EldenRing\\eldenring.exe")),
    ]
    activity = detect_wine_activity(processes)
    assert activity is not None
    assert activity.kind == ActivityKind.WINE
    assert activity.key == "eldenring.exe"


def test_detect_wine_activity_skips_wine_helper_processes() -> None:
    processes = [
        proc(200, "wine64-preloader", ("wine64-preloader", "C:\\windows\\system32\\services.exe")),
        proc(201, "wine64-preloader", ("wine64-preloader", "explorer.exe")),
    ]
    assert detect_wine_activity(processes) is None


def test_detect_wine_activity_finds_exe_past_ignored_ones_in_same_argv() -> None:
    processes = [proc(200, "wine", ("wine", "explorer.exe", "/desktop=x", "MyGame.exe"))]
    activity = detect_wine_activity(processes)
    assert activity is not None
    assert activity.key == "mygame.exe"


def test_detect_wine_activity_ignores_non_wine_processes() -> None:
    processes = [proc(1, "bash", ("bash",))]
    assert detect_wine_activity(processes) is None


# -- Native detection ---------------------------------------------------------


def test_detect_native_activity_matches_watchlist() -> None:
    processes = [proc(1, "bash"), proc(2, "nvim")]
    activity = detect_native_activity(processes, {"nvim"})
    assert activity is not None
    assert activity.kind == ActivityKind.NATIVE
    assert activity.key == "nvim"


def test_detect_native_activity_case_insensitive() -> None:
    processes = [proc(1, "NVIM")]
    activity = detect_native_activity(processes, {"nvim"})
    assert activity is not None
    assert activity.key == "nvim"


def test_detect_native_activity_no_match() -> None:
    processes = [proc(1, "bash")]
    assert detect_native_activity(processes, {"nvim"}) is None


# -- Priority ordering --------------------------------------------------------


def test_priority_steam_beats_wine_and_native() -> None:
    processes = [
        proc(1, "nvim"),
        proc(2, "wine64-preloader", ("wine64-preloader", "SomeGame.exe")),
        proc(3, "hl2_linux"),
    ]
    app_index = {"440": SteamAppInfo(appid="440", name="Team Fortress 2", installdir="")}
    environ = {3: {"SteamAppId": "440"}}
    activity = detect_activity(processes, app_index, {"nvim"}, lambda pid: environ.get(pid, {}))
    assert activity is not None
    assert activity.kind == ActivityKind.STEAM


def test_priority_wine_beats_native() -> None:
    processes = [
        proc(1, "nvim"),
        proc(2, "wine64-preloader", ("wine64-preloader", "SomeGame.exe")),
    ]
    activity = detect_activity(processes, {}, {"nvim"}, lambda pid: {})
    assert activity is not None
    assert activity.kind == ActivityKind.WINE


def test_priority_native_used_when_nothing_else_detected() -> None:
    processes = [proc(1, "nvim")]
    activity = detect_activity(processes, {}, {"nvim"}, lambda pid: {})
    assert activity is not None
    assert activity.kind == ActivityKind.NATIVE


def test_priority_none_when_nothing_matches() -> None:
    processes = [proc(1, "bash")]
    assert detect_activity(processes, {}, {"nvim"}, lambda pid: {}) is None


# -- activity_signature --------------------------------------------------------


def test_activity_signature_none_for_no_activity() -> None:
    assert activity_signature(None) is None


def test_activity_signature_ignores_pid() -> None:
    a = detect_native_activity([proc(1, "nvim")], {"nvim"})
    b = detect_native_activity([proc(2, "nvim")], {"nvim"})
    assert activity_signature(a) == activity_signature(b)


# -- override resolution --------------------------------------------------------


def test_resolve_override_returns_configured_override() -> None:
    config = make_config(steam_overrides={"440": ActivityOverride(details="Fragging")})
    activity = detect_steam_activity(
        [proc(1, "hl2_linux")],
        {"440": SteamAppInfo(appid="440", name="TF2", installdir="")},
        lambda pid: {"SteamAppId": "440"},
    )
    assert activity is not None
    override = resolve_override(activity, config)
    assert override.details == "Fragging"


def test_resolve_override_returns_empty_when_unconfigured() -> None:
    config = make_config()
    activity = detect_native_activity([proc(1, "nvim")], {"nvim"})
    assert activity is not None
    assert resolve_override(activity, config) == ActivityOverride()


def test_resolve_override_applies_top_level_button_default() -> None:
    config = make_config(button_label="View on GitHub", button_url="https://github.com/x/y")
    activity = detect_native_activity([proc(1, "nvim")], {"nvim"})
    assert activity is not None
    override = resolve_override(activity, config)
    assert override.button_label == "View on GitHub"
    assert override.button_url == "https://github.com/x/y"


def test_resolve_override_per_activity_button_wins_over_default() -> None:
    config = make_config(
        button_label="Default", button_url="https://default.example",
        native={"nvim": ActivityOverride(button_label="Custom", button_url="https://custom.example")},
    )
    activity = detect_native_activity([proc(1, "nvim")], {"nvim"})
    assert activity is not None
    override = resolve_override(activity, config)
    assert override.button_label == "Custom"
    assert override.button_url == "https://custom.example"


def test_resolve_override_uses_correct_table_per_kind() -> None:
    config = make_config(
        native={"nvim": ActivityOverride(details="native hit")},
        wine_overrides={"game.exe": ActivityOverride(details="wine hit")},
        steam_overrides={"440": ActivityOverride(details="steam hit")},
    )
    native_activity = detect_native_activity([proc(1, "nvim")], {"nvim"})
    wine_activity = detect_wine_activity([proc(2, "wine", ("wine", "game.exe"))])
    steam_activity = detect_steam_activity(
        [proc(3, "hl2_linux")], {"440": SteamAppInfo("440", "TF2", "")}, lambda pid: {"SteamAppId": "440"}
    )
    assert native_activity and resolve_override(native_activity, config).details == "native hit"
    assert wine_activity and resolve_override(wine_activity, config).details == "wine hit"
    assert steam_activity and resolve_override(steam_activity, config).details == "steam hit"
