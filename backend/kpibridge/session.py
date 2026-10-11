"""CLI utilisé par le lanceur : `python -m kpibridge.session new` affiche l'URL d'ouverture à usage unique."""

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
    # Le secret est dans le fragment : il n'est jamais envoyé dans une requête HTTP ni écrit dans un journal serveur.
    print(f"http://127.0.0.1:{settings.port}/#token={token}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
