"""Print a stock creator's name the way a finished command has to.

Run by CI on Windows, where it is the only thing that can catch this class.
pytest cannot: its capture is utf-8 on every platform, so a cp1252 console never
appears in the suite. Only a real entry point writing to a real pipe does.

The failure this guards was not hypothetical. `vidsmith meta` wrote youtube.json,
youtube.txt and description.txt correctly, then raised UnicodeEncodeError
printing the credits block, because a Pexels photographer's name contains U+1ECB
and a Windows console is cp1252. Exit 1 over work that had entirely succeeded.

Kept as a file rather than a `python -c` one-liner in the workflow so the name
can be literal UTF-8 source. Spelling it with \\u escapes through two levels of
shell quoting was its own small trap, and this module's whole subject is what
happens when text survives one layer and not the next.
"""
from __future__ import annotations

import sys

from vidsmith.cli import _printable_console

# Real credits from a real build. U+1EC5, U+1ECB and U+1ED3 are all outside
# cp1252; the first alone is enough to end a command.
NAMES = [
    "Nguyễn Thị Hồng",
    "Ono  Kosuki",
    "tom analogicus",
]


def main() -> int:
    _printable_console()
    print(f"stdout encoding after the fix: {sys.stdout.encoding}")
    for name in NAMES:
        print(f"  credit: {name}")
    print("a name outside cp1252 reached a pipe without killing the command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
