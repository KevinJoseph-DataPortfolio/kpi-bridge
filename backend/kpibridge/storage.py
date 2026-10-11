"""Stockage privé des projets : noms générés, chemins résolus côté serveur uniquement."""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path

from .config import Settings

_ID_RE = re.compile(r"^[a-z]{1,4}_[0-9a-f]{20}$")


def check_id(value: str) -> str:
    if not _ID_RE.match(value or ""):
        raise ValueError("identifiant invalide")
    return value


def _within(base: Path, target: Path) -> Path:
    base_r = base.resolve()
    target_r = target.resolve()
    if base_r != target_r and base_r not in target_r.parents:
        raise ValueError("chemin hors du stockage")
    return target_r


def project_dir(settings: Settings, project_id: str) -> Path:
    check_id(project_id)
    return _within(settings.projects_dir, settings.projects_dir / project_id)


def ensure_project_dirs(settings: Settings, project_id: str) -> Path:
    base = project_dir(settings, project_id)
    for sub in ("sources", "raw", "ds", "exports", "work"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    restrict(base)
    return base


def project_path(settings: Settings, project_id: str, *parts: str) -> Path:
    for p in parts:
        if not re.match(r"^[A-Za-z0-9_.-]{1,120}$", p) or p in {".", ".."}:
            raise ValueError("segment de chemin invalide")
    base = project_dir(settings, project_id)
    return _within(base, base.joinpath(*parts))


def restrict(path: Path) -> None:
    """Permissions restrictives (POSIX). Sous Windows, le profil utilisateur porte déjà l'ACL."""
    try:
        if path.is_dir():
            os.chmod(path, stat.S_IRWXU)
        elif path.exists():
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def make_readonly(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR)
    except OSError:
        pass


def _onerror(func, path, _exc):  # type: ignore[no-untyped-def]
    try:
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
        func(path)
    except OSError:
        pass


def delete_project_files(settings: Settings, project_id: str) -> None:
    base = project_dir(settings, project_id)
    if base.exists():
        shutil.rmtree(base, onexc=_onerror)


def sweep_tmp(settings: Settings) -> int:
    """Supprime les temporaires restés après un arrêt brutal."""
    count = 0
    if settings.tmp_dir.exists():
        for child in settings.tmp_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, onexc=_onerror)
            else:
                try:
                    child.unlink()
                except OSError:
                    continue
            count += 1
    if settings.projects_dir.exists():
        for proj in settings.projects_dir.iterdir():
            work = proj / "work"
            if work.is_dir():
                for child in work.iterdir():
                    shutil.rmtree(child, onexc=_onerror) if child.is_dir() else child.unlink(missing_ok=True)
                    count += 1
            exports = proj / "exports"
            if exports.is_dir():
                for child in exports.iterdir():
                    child.unlink(missing_ok=True)
                    count += 1
    return count
