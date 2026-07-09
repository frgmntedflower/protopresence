from protopresence.config import ActivityOverride
from protopresence.detect import Activity, ActivityKind
from protopresence.presence import DiscordPresenceManager, build_presence_payload


def test_build_presence_payload_falls_back_to_display_name() -> None:
    activity = Activity(kind=ActivityKind.NATIVE, key="nvim", display_name="nvim", pid=1)
    payload = build_presence_payload(activity, ActivityOverride())
    assert payload == {"details": "nvim"}


def test_build_presence_payload_override_details_wins() -> None:
    activity = Activity(kind=ActivityKind.STEAM, key="440", display_name="Team Fortress 2", pid=1)
    payload = build_presence_payload(activity, ActivityOverride(details="Fragging"))
    assert payload["details"] == "Fragging"


def test_build_presence_payload_includes_only_set_optional_fields() -> None:
    activity = Activity(kind=ActivityKind.NATIVE, key="nvim", display_name="nvim", pid=1)
    override = ActivityOverride(large_image="nvim_logo", large_text="Neovim")
    payload = build_presence_payload(activity, override)
    assert payload == {"details": "nvim", "large_image": "nvim_logo", "large_text": "Neovim"}
    assert "state" not in payload
    assert "small_image" not in payload


def test_build_presence_payload_steam_defaults_to_header_image() -> None:
    activity = Activity(kind=ActivityKind.STEAM, key="440", display_name="Team Fortress 2", pid=1)
    payload = build_presence_payload(activity, ActivityOverride())
    assert payload["large_image"] == "https://cdn.cloudflare.steamstatic.com/steam/apps/440/header.jpg"
    assert payload["large_text"] == "Team Fortress 2"


def test_build_presence_payload_steam_header_image_available_without_a_resolved_name() -> None:
    activity = Activity(kind=ActivityKind.STEAM, key="3291080", display_name="Playing a Steam Game", pid=1)
    payload = build_presence_payload(activity, ActivityOverride())
    assert payload["large_image"] == "https://cdn.cloudflare.steamstatic.com/steam/apps/3291080/header.jpg"


def test_build_presence_payload_steam_override_image_wins() -> None:
    activity = Activity(kind=ActivityKind.STEAM, key="440", display_name="Team Fortress 2", pid=1)
    override = ActivityOverride(large_image="tf2_logo", large_text="TF2")
    payload = build_presence_payload(activity, override)
    assert payload["large_image"] == "tf2_logo"
    assert payload["large_text"] == "TF2"


def test_build_presence_payload_non_steam_has_no_default_image() -> None:
    activity = Activity(kind=ActivityKind.WINE, key="game.exe", display_name="game.exe", pid=1)
    payload = build_presence_payload(activity, ActivityOverride())
    assert "large_image" not in payload


def test_presence_manager_connect_failure_does_not_raise(monkeypatch) -> None:
    class ExplodingPresence:
        def __init__(self, client_id: str) -> None:
            pass

        def connect(self) -> None:
            raise RuntimeError("Discord is not running")

    monkeypatch.setattr("protopresence.presence.Presence", ExplodingPresence)
    manager = DiscordPresenceManager("client-id")
    manager.update({"details": "x"}, is_new_activity=True)  # must not raise
    assert manager.connected is False


def test_presence_manager_update_failure_marks_disconnected(monkeypatch) -> None:
    class FlakyPresence:
        def __init__(self, client_id: str) -> None:
            pass

        def connect(self) -> None:
            pass

        def update(self, **kwargs) -> None:
            raise RuntimeError("pipe closed")

    monkeypatch.setattr("protopresence.presence.Presence", FlakyPresence)
    manager = DiscordPresenceManager("client-id")
    manager.update({"details": "x"}, is_new_activity=True)
    assert manager.connected is False


def test_presence_manager_successful_round_trip(monkeypatch) -> None:
    calls = []

    class FakePresence:
        def __init__(self, client_id: str) -> None:
            self.client_id = client_id

        def connect(self) -> None:
            calls.append("connect")

        def update(self, **kwargs) -> None:
            calls.append(("update", kwargs))

        def clear(self) -> None:
            calls.append("clear")

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr("protopresence.presence.Presence", FakePresence)
    manager = DiscordPresenceManager("client-id")
    manager.update({"details": "Playing"}, is_new_activity=True)
    assert manager.connected is True
    manager.clear()
    manager.close()
    assert calls[0] == "connect"
    assert calls[1][0] == "update"
    assert calls[1][1]["details"] == "Playing"
    assert "clear" in calls
    assert "close" in calls
