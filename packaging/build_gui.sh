#!/usr/bin/env bash
# Builds a standalone, single-file Linux binary of the protopresence GUI
# using PyInstaller. The output doesn't need Python installed to run --
# just Tk's shared libraries, which are present on any desktop Linux system
# that already has tkinter working.
#
# Usage:
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -e ".[build]"
#   ./packaging/build_gui.sh
#
# Output: dist/protopresence-gui

set -euo pipefail
cd "$(dirname "$0")/.."

pyinstaller \
    --onefile \
    --windowed \
    --name protopresence-gui \
    --paths src \
    --add-data "src/protopresence/assets:protopresence/assets" \
    packaging/gui_launcher.py

echo
echo "Built: dist/protopresence-gui"
