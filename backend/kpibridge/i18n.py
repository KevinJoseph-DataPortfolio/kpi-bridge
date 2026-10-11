"""Traductions côté serveur (exports, libellés de suggestions). Source unique : `shared/locales/<lang>.json`,
également utilisée par le frontend."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

LANGS = ("fr", "en", "ru", "zh-Hans", "de", "es")


def locales_dir() -> Path:
    env = os.environ.get("KPI_LOCALES_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "shared" / "locales"
        if cand.is_dir():
            return cand
    return here.parent / "locales"


@lru_cache(maxsize=8)
def load(lang: str) -> dict[str, Any]:
    if lang not in LANGS:
        lang = "fr"
    with open(locales_dir() / f"{lang}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _lookup(data: dict[str, Any], key: str) -> Any:
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def translate(lang: str, key: str, params: dict[str, Any] | None = None) -> str:
    value = _lookup(load(lang), key)
    if value is None and lang != "en":
        value = _lookup(load("en"), key)
    if not isinstance(value, str):
        return key
    params = params or {}
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(params.get(m.group(1), m.group(0))), value)
