"""CLI used by the launcher: `python -m kpibridge.session new` prints the single-use opening URL."""

from __future__ import annotations

import sys

from .config import ensure_dirs, get_settings
from .security import create_bootstrap_token


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "new":
        print("usage: python -m kpibridge.session new", file=sys.stderr)
        return 2
    settings = get_settings()
    ensure_dirs(settings)
    token = create_bootstrap_token(settings)
    # The secret is in the fragment: it is never sent in an HTTP request nor written to a server log.
    print(f"http://127.0.0.1:{settings.port}/#token={token}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
