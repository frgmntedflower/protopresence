"""A small single-page Tkinter GUI for protopresence.

This is a frontend, not a second implementation: starting the daemon here
runs the exact same `daemon.run()` poll loop as the CLI, just on a
background thread inside this process instead of under systemd. It exists
for people who'd rather click "Start" and fill in two text fields than
write TOML by hand.

Requires the system Tk bindings (the `python3-tk` package on Debian/Ubuntu,
`tk` on Arch, `python3-tkinter` on Fedora) -- these ship separately from
Python itself on most distros.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import tkinter as tk
import tomllib
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from . import __version__
from .config import ConfigError, load_config, set_top_level_scalar
from .daemon import DEFAULT_CONFIG_PATH, PollStatus, run


def _package_dir() -> Path:
    """Resolve the package directory, whether running from source or a PyInstaller bundle."""
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base is not None:
        return Path(frozen_base) / "protopresence"
    return Path(__file__).parent


ICON_PATH = _package_dir() / "assets" / "icon.png"


class ProtopresenceGUI:
    """The single-window app: status at the top, start/stop, then config fields."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.root = tk.Tk()
        self.root.title("protopresence")
        self.root.resizable(False, False)
        self._icon_image: tk.PhotoImage | None = None
        if ICON_PATH.exists():
            self._icon_image = tk.PhotoImage(file=str(ICON_PATH))
            self.root.iconphoto(True, self._icon_image)

        self.status_var = tk.StringVar(value="Stopped")
        self.activity_var = tk.StringVar(value="—")
        self.discord_var = tk.StringVar(value="—")
        self.client_id_var = tk.StringVar()
        self.poll_interval_var = tk.StringVar()

        self._build_widgets()
        self._load_config_into_fields()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ---------------------------------------------------------

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}
        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="w", pady=(0, 8))
        if self._icon_image is not None:
            ttk.Label(header, image=self._icon_image).grid(row=0, column=0, padx=(0, 8))
        title_box = ttk.Frame(header)
        title_box.grid(row=0, column=1, sticky="w")
        ttk.Label(title_box, text="protopresence", font=("Sans", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text=f"v{__version__}", foreground="#888").grid(row=1, column=0, sticky="w")

        status_frame = ttk.LabelFrame(outer, text="Status", padding=8)
        status_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        status_frame.columnconfigure(1, weight=1)
        ttk.Label(status_frame, text="Daemon:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Label(status_frame, textvariable=self.status_var).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(status_frame, text="Activity:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Label(status_frame, textvariable=self.activity_var, wraplength=280).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(status_frame, text="Discord:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Label(status_frame, textvariable=self.discord_var).grid(row=2, column=1, sticky="w", **pad)

        self.toggle_button = ttk.Button(outer, text="Start", command=self._on_toggle)
        self.toggle_button.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        config_frame = ttk.LabelFrame(outer, text="Configuration", padding=8)
        config_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        config_frame.columnconfigure(1, weight=1)

        ttk.Label(config_frame, text="Discord Client ID:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(config_frame, textvariable=self.client_id_var, width=26).grid(row=0, column=1, sticky="ew", **pad)

        ttk.Label(config_frame, text="Poll interval (sec):").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(config_frame, textvariable=self.poll_interval_var, width=8).grid(row=1, column=1, sticky="w", **pad)

        button_row = ttk.Frame(config_frame)
        button_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(button_row, text="Save", command=self._on_save).pack(side="left", padx=(8, 4))
        ttk.Button(button_row, text="Open config file", command=self._on_open_config).pack(side="left", padx=4)

        ttk.Label(
            outer,
            text=(
                "Native/Steam/Wine overrides ([native], [steam_overrides], [wine_overrides])\n"
                "are edited directly in the config file -- use \"Open config file\" above."
            ),
            foreground="#888",
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(0, 4))
        ttk.Label(outer, text=str(self.config_path), foreground="#888").grid(row=5, column=0, sticky="w")

    # -- config <-> fields ------------------------------------------------

    def _load_config_into_fields(self) -> None:
        data: dict = {}
        if self.config_path.is_file():
            try:
                data = tomllib.loads(self.config_path.read_text())
            except tomllib.TOMLDecodeError:
                data = {}
        self.client_id_var.set(str(data.get("client_id", "")))
        self.poll_interval_var.set(str(data.get("poll_interval", 15.0)))

    def _on_save(self) -> None:
        client_id = self.client_id_var.get().strip()
        if not client_id:
            messagebox.showerror("protopresence", "Client ID can't be empty.")
            return
        try:
            poll_interval = float(self.poll_interval_var.get().strip())
            if poll_interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("protopresence", "Poll interval must be a positive number.")
            return

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        text = self.config_path.read_text() if self.config_path.is_file() else ""
        text = set_top_level_scalar(text, "client_id", json.dumps(client_id))
        text = set_top_level_scalar(text, "poll_interval", repr(poll_interval))
        self.config_path.write_text(text)
        messagebox.showinfo("protopresence", "Saved. Stop and start the daemon to apply changes.")

    def _on_open_config(self) -> None:
        if not self.config_path.is_file():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text('client_id = ""\n')
        try:
            subprocess.Popen(["xdg-open", str(self.config_path)])
        except (OSError, FileNotFoundError):
            webbrowser.open(f"file://{self.config_path}")

    # -- start/stop -------------------------------------------------------

    def _on_toggle(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            messagebox.showerror("protopresence", f"Can't start: {exc}")
            return

        self.stop_event = threading.Event()
        self.worker = threading.Thread(
            target=run,
            kwargs={"config": config, "stop_event": self.stop_event, "on_status": self._on_status},
            daemon=True,
        )
        self.worker.start()
        self.status_var.set("Running")
        self.toggle_button.config(text="Stop")

    def _stop(self) -> None:
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=2)
        self.status_var.set("Stopped")
        self.toggle_button.config(text="Start")
        self.activity_var.set("—")
        self.discord_var.set("—")

    def _on_status(self, status: PollStatus) -> None:
        # Called from the worker thread; Tk widgets may only be touched from
        # the main thread, so hop over via `after`.
        self.root.after(0, self._apply_status, status)

    def _apply_status(self, status: PollStatus) -> None:
        if status.activity is None:
            self.activity_var.set("Idle (nothing tracked is running)")
        else:
            self.activity_var.set(f"[{status.activity.kind.value}] {status.activity.display_name}")
        self.discord_var.set("Connected" if status.discord_connected else "Not connected")

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.stop_event.set()
            self.worker.join(timeout=2)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="protopresence-gui", description="Simple GUI for protopresence")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml")
    args = parser.parse_args(argv)

    app = ProtopresenceGUI(args.config)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
