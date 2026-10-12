"""Archive de livraison versionnée : fichiers suivis par Git + interface compilée (frontend/dist).
Exclut données utilisateur, secrets, caches, environnements et modèles (rien de tout cela n'est suivi par Git).

Usage : python scripts/make_release.py  →  dist-release/kpi-bridge-<version>.zip + .sha256
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from kpibridge import __version__  # noqa: E402

FORBIDDEN = (".env", ".venv/", "node_modules/", "data/", ".sqlite3", "models/")


def main() -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if not dist.exists():
        sys.exit("frontend/dist absent : lancez d'abord `npm run build` dans frontend/")
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    files += [str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "frontend" / "dist").rglob("*") if p.is_file()]
    for f in files:
        if any(f == x or f.startswith(x) or f.endswith(x) for x in FORBIDDEN):
            sys.exit(f"fichier interdit dans l'archive : {f}")
    out_dir = ROOT / "dist-release"
    out_dir.mkdir(exist_ok=True)
    name = f"kpi-bridge-{__version__}"
    archive = out_dir / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in sorted(set(files)):
            zf.write(ROOT / f, f"{name}/{f}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out_dir / f"{name}.zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"{archive} ({archive.stat().st_size / 2**20:.1f} Mio, {len(set(files))} fichiers)\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
