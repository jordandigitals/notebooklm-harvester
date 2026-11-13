"""Entry point of the packaged app (PyInstaller).

Double-click starts the local page, or shows it if it is already running. The packaged
executable also runs the library's command line for sign-in sub-commands, because a
packaged app has no separate Python to run `python -m notebooklm`.
"""

import os
import sys

from nlh import adapter


def main() -> int:
    """Dispatch: library sub-command, or the app itself."""
    if len(sys.argv) > 1 and sys.argv[1] == adapter.LIBRARY_CLI_FLAG:
        adapter.run_library_cli(sys.argv[2:])
        return 0
    from nlh.server import serve

    return serve(open_browser=os.environ.get("NLH_NO_BROWSER") != "1")


if __name__ == "__main__":
    raise SystemExit(main())
