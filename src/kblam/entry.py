"""The `kblam` console script.

`kblam hook <event>` runs on every Write, Edit and Bash call an agent makes, so it is dispatched
without importing the rest of the CLI (the Jev client alone takes about 0.2 s to import). Every
other command goes to kblam.cli.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _hook_call(argv: list[str]) -> tuple[str, Path | None] | None:
    """(event, --root) if argv is `[--root DIR] hook EVENT`, else None."""
    root = None
    if len(argv) >= 2 and argv[0] == "--root":
        root, argv = Path(argv[1]), argv[2:]
    elif argv and argv[0].startswith("--root="):
        root, argv = Path(argv[0].split("=", 1)[1]), argv[1:]
    if len(argv) == 2 and argv[0] == "hook" and not argv[1].startswith("-"):
        return argv[1], root
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    call = _hook_call(argv)
    if call is not None:
        from kblam.hook import run

        return run(*call)
    from kblam.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
