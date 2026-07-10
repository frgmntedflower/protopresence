"""PyInstaller entry point for the GUI.

PyInstaller runs whatever script it's pointed at as `__main__`, with no
parent package -- so if it were pointed directly at `protopresence/gui.py`,
that module's own relative imports (`from .config import ...`) would break
with "attempted relative import with no known parent package". Importing
the module by its full dotted path here, from *outside* the package,
avoids that: `gui.py` gets imported normally as `protopresence.gui`, with
`__package__` set correctly, so its relative imports resolve as expected.
"""

from protopresence.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
