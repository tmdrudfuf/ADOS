"""ADOS command-line interface."""

from __future__ import annotations

import sys

from .cli_app import CliApplication


def main(argv: list[str] | None = None) -> int:
    return CliApplication().run(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
