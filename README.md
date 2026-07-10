<p align="center">
  <img src=".github/assets/logo.png" width="96" height="96" alt="protopresence logo">
</p>

<h1 align="center">protopresence</h1>

<p align="center">Automatic Discord Rich Presence for Steam, Proton, Wine, and whatever else you're running on Linux.</p>

---

I got tired of Discord showing nothing while I was deep in a Proton game, and I didn't want to hand-write a presence integration for every single game I own. protopresence watches your running processes, figures out what you're actually doing, and tells Discord about it. No per-game setup, no Steam Web API key, and protopresence itself never makes a network call — everything it knows comes from files already sitting on your disk (it does hand Discord a Steam CDN image URL for game art, which Discord's own client fetches, same as it fetches your friends' avatars).

It runs as a small background daemon, and there's also a simple GUI if you'd rather not live in a terminal.

## How it actually detects what you're playing

This is the part I think is worth explaining, because it's the whole reason this tool exists instead of just being "yet another wrapper around a game list."

When Steam launches a game — and this includes Windows games running through Proton, not just native Linux builds — it sets a `SteamAppId` environment variable on that process. That variable is invisible to the game itself in any way that matters, but it's sitting right there in `/proc/<pid>/environ` for anyone with permission to read it. Since protopresence runs as your user, it can read the environ of any other process you own, Proton wrapper or otherwise.

So the detection loop is:

1. Walk `/proc`, look at every PID you own, check its environment for `SteamAppId` (or `SteamGameId`, which shows up for some non-Steam-library cases). Found one? That's your Steam AppID, whether it's TF2 running natively or Elden Ring running through GE-Proton.
2. Take that AppID and cross-reference it against `appmanifest_<id>.acf` files sitting in your local Steam library folders (`~/.steam/steam`, `~/.local/share/Steam`, the Flatpak path, plus anything in `libraryfolders.vdf`). This gets you the game's actual name without ever touching the network. If the game isn't installed under a library path protopresence recognizes, it falls back to Steam's own local `appinfo.vdf` client cache instead — this is the same on-disk database the Steam client keeps of every app it has ever seen (owned, installed or not), and it's what finally let me stop seeing "App 3291080" for a game I'd installed to a weird custom folder outside the usual library layout. You never see a raw AppID out of this — worst case, if truly nothing local knows the game, it just says "Playing a Steam Game."

   For the presence's picture, protopresence hands Discord Steam's public per-AppID store header image URL (`https://cdn.cloudflare.steamstatic.com/steam/apps/<id>/header.jpg`). protopresence itself never fetches it — it just gives Discord the URL, and Discord's own client (which is already talking to the internet) is what actually loads it, the same as it would for any external image URL in a presence payload. A handful of unlisted or delisted titles don't have a header image at that URL; in that case Discord just shows no image, nothing breaks.
3. If nothing Steam-related is running, look for bare Wine processes (`wine`, `wine64`, `wine-preloader`) that aren't Steam-managed — this is your Lutris/Bottles/raw-`wine` case. Dig the real `.exe` out of the process's argv, skipping past Wine's own bookkeeping processes (`services.exe`, `explorer.exe`, `winedevice.exe`, and so on) to find the actual game.
4. If nothing matched, fall back to a plain watch-list of native app process names you configure yourself (editor, terminal, whatever you want).

Only one thing is ever shown at a time, and games always win over native apps. The whole thing is priority-ordered and stops at the first match — it doesn't try to merge or rank multiple detections.

Because this is all local, there's no Steam Web API key to request, no rate limits to worry about, and nothing that breaks if you're offline.

## Setup

### 1. Make a Discord application

Rich Presence needs a Discord "application" to post as — this is just an ID number, your Discord account isn't otherwise involved.

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Name it whatever you want; the name doesn't show up anywhere in your presence.
2. On the **General Information** page, copy the **Application ID**. That's your `client_id`.
3. If you want custom art (a game logo, a small icon in the corner) instead of the plain text-only presence, go to **Rich Presence → Art Assets** and upload images there. Whatever you name an asset when you upload it is the string you'll use for `large_image` / `small_image` in your config — there's no other way to reference art besides uploading it here first.

That's it, no OAuth, no bot token, nothing else needed.

### 2. Install

```bash
git clone https://github.com/frgmntedflower/protopresence.git
cd protopresence
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

This installs two commands: `protopresence` (the daemon) and `protopresence-gui` (the GUI, if you have Tk installed — see below).

### 3. Configure

```bash
mkdir -p ~/.config/protopresence
cp config.example.toml ~/.config/protopresence/config.toml
```

Open it up and at minimum set `client_id` to the Application ID from step 1. Everything else has a reasonable default. See [Config reference](#config-reference) below for the full picture.

### 4. Run it

Either run it directly to try it out:

```bash
protopresence --verbose
```

...or install it as a systemd user service so it starts automatically and survives you closing the terminal — see [Running as a service](#running-as-a-service).

## The GUI

If you'd rather not manage a systemd unit or edit TOML by hand, `protopresence-gui` gives you a single window: current status, a start/stop button, and fields for the two settings you're most likely to actually change (Client ID, poll interval). Anything more advanced — the override tables — you edit in the config file directly; there's a button in the GUI that opens it in your default editor.

```bash
protopresence-gui
```

The GUI needs Tk, which ships separately from Python on most distros:

```bash
# Debian / Ubuntu
sudo apt install python3-tk

# Fedora
sudo dnf install python3-tkinter

# Arch
sudo pacman -S tk
```

Starting the daemon from the GUI runs the exact same poll loop as the `protopresence` CLI command, just on a background thread inside the GUI process — it's the same code either way, not a second implementation.

## Config reference

Config lives at `~/.config/protopresence/config.toml`. Full annotated example in [`config.example.toml`](config.example.toml).

| Key | Type | Default | Notes |
|---|---|---|---|
| `client_id` | string | *required* | Your Discord application ID |
| `poll_interval` | number | `15.0` | Seconds between checks. Discord itself won't take updates much faster than 15s anyway |
| `extra_steam_roots` | list of strings | `[]` | Extra Steam install locations, beyond the defaults protopresence already checks |
| `button_label` / `button_url` | strings | *(none)* | A clickable button shown on every presence card; both or neither |
| `[native]` | table | `{}` | Watch-list of process names to show a presence for when no game is running |
| `[steam_overrides]` | table | `{}` | Keyed by numeric Steam AppID |
| `[wine_overrides]` | table | `{}` | Keyed by the `.exe` basename (lowercase) of a bare Wine game |

Each entry in the three override tables can be:

- a **bare string**, which is shorthand for `details` — e.g. `nvim = "Editing code"`
- `true`, meaning "track this, but don't customize anything" — the auto-generated name is used as-is
- a **table** with any of: `details`, `state`, `large_image`, `large_text`, `small_image`, `small_text`, `button_label`, `button_url`

A `button_label`/`button_url` inside a specific override wins over the top-level default, just for that game/app. Both fields have to be set together (`button_url` must be `http://` or `https://`), and — a Discord quirk, not a protopresence one — the button never shows up on *your own* client; it's visible to other people looking at your profile.

```toml
[native]
nvim = "Editing code"
code = { details = "Writing code", large_image = "vscode", large_text = "VS Code" }

[steam_overrides]
440 = { details = "Fragging in TF2", large_image = "tf2_logo", large_text = "Team Fortress 2" }
730 = "Playing Counter-Strike 2"

[wine_overrides]
"eldenring.exe" = { details = "Dying in the Lands Between", large_image = "elden_ring" }
```

If you don't configure an override for something protopresence detects, it still shows *something* sensible: the game's real name from its Steam manifest, or the raw process/exe name otherwise.

## Running as a service

A systemd **user** unit is included at `packaging/protopresence.service`, so it runs in the background under your own session (no root needed) and starts on login.

```bash
mkdir -p ~/.config/systemd/user
cp packaging/protopresence.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now protopresence.service
```

Check it's actually running and see the logs:

```bash
systemctl --user status protopresence.service
journalctl --user -u protopresence.service -f
```

If you installed with `pip install --user` or pipx instead of a venv, the `ExecStart` path in the unit file (`%h/.local/bin/protopresence`) might need adjusting — check `which protopresence` and update the unit accordingly, then `systemctl --user daemon-reload`.

## Troubleshooting

**Discord isn't picking up the presence at all.** protopresence talks to Discord over a local Unix socket (`$XDG_RUNTIME_DIR/discord-ipc-0`), which only exists while a Discord client is actually running and logged in. If Discord was closed when protopresence started, that's fine — it retries the connection every poll tick — but if it's still not showing after Discord is open, check the logs with `--verbose`; every connection and update failure gets logged.

**Discord is running but installed as a Flatpak.** Flatpak sandboxes Discord's filesystem access, which changes where its IPC socket actually lives (usually under `~/.var/app/com.discordapp.Discord/...` instead of the normal `$XDG_RUNTIME_DIR`). `pypresence` looks in the standard locations, so a sandboxed Discord may need either `--filesystem=xdg-run/discord-ipc-0` added to its Flatpak permissions (via Flatseal or `flatpak override`), or a symlink from the expected path to Flatpak's actual socket location. If Discord itself isn't Flatpak'd, this doesn't apply to you.

**A game or process isn't being detected.** A few possibilities:
- For Steam games: is it actually launched *through* Steam (even if just "Add a Non-Steam Game")? If you ran the binary directly outside Steam, `SteamAppId` was never set, and protopresence has no way to know it's a Steam game — it'll fall through to the Wine/native checks instead.
- For bare Wine games: make sure the actual game process shows up in `wine`/`wine64`/`*-preloader`'s argv with an `.exe` name that isn't in the ignored helper-process list (`services.exe`, `explorer.exe`, etc). Some launchers wrap things in ways that hide the real exe name — check with `ps aux | grep -i wine`.
- Run `protopresence --once --verbose` to see exactly one detection pass with full logging, without waiting on the poll loop.

**Permission errors reading `/proc/<pid>/environ`.** This is expected and handled — you can only read the environment of processes you own. protopresence silently skips any PID it can't read (this is the vast majority of PIDs on your system, since most belong to other users or root). If the game process itself is somehow owned by a different user than the one running protopresence, though, there's genuinely nothing protopresence can do about that — the kernel won't let it read that environ regardless.

**Config won't load / daemon won't start.** protopresence fails loudly on bad config, on purpose — better to know immediately than have it silently limp along with half-understood settings. The error message tells you exactly what's wrong (missing `client_id`, bad TOML syntax, unknown override field, etc). Fix the file and restart.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite covers the pure-logic pieces — VDF/manifest parsing, config validation, override resolution, and detection priority ordering — with `/proc` and psutil mocked out. It doesn't need a real Steam or Discord install to run.

## License

MIT, see [LICENSE](LICENSE).
