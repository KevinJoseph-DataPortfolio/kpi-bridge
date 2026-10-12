"""Inventaire des licences des dépendances Python installées et refus des licences incompatibles (GPL/AGPL/SSPL).

Usage : python scripts/check_licenses.py [--markdown]
"""

from __future__ import annotations

import sys
from importlib import metadata

FORBIDDEN = ("AGPL", "Affero", "SSPL", "GNU General Public License v3", "GPLv3", "GPL-3")
ALLOWED_EXCEPTIONS = {
    # Bibliothèques LGPL/doubles licences acceptables en usage non modifié : aucune actuellement.
}
DEV_ONLY = {"pytest", "ruff", "mypy", "xlwt", "fonttools", "pip-audit", "iniconfig", "pluggy", "pygments", "mypy-extensions", "pathspec", "librt"}


def license_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return " / ".join(classifiers)
    lic = (meta.get("License") or "").strip().splitlines()
    return lic[0][:80] if lic else "UNKNOWN"


def main() -> int:
    rows = []
    bad = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"] or "?"
        lic = license_of(dist)
        rows.append((name, dist.version, lic))
        if any(f.lower() in lic.lower() for f in FORBIDDEN) and name.lower() not in ALLOWED_EXCEPTIONS and name.lower() not in DEV_ONLY:
            bad.append((name, lic))
    if "--markdown" in sys.argv:
        print("| Paquet | Version | Licence |\n|---|---|---|")
        for r in rows:
            print(f"| {r[0]} | {r[1]} | {r[2]} |")
    else:
        for r in rows:
            print(f"{r[0]:32s} {r[1]:14s} {r[2]}")
    if bad:
        print("\nLicences incompatibles :", bad, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
