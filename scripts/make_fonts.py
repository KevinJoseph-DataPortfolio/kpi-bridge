"""Builds the static (Regular weight) fonts embedded in the exports, from the official Noto variable fonts
(google/fonts, SIL OFL 1.1 licence). Re-run only to change the font version.

Usage: python scripts/make_fonts.py <NotoSans[wdth,wght].ttf> <NotoSansSC[wght].ttf>
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

OUT = Path(__file__).resolve().parents[1] / "backend" / "kpibridge" / "exports" / "fonts"


def make(src: str, dest: str, axes: dict[str, float]) -> None:
    font = TTFont(src)
    static = instantiateVariableFont(font, axes, updateFontNames=True)
    static.save(OUT / dest)
    print(dest, (OUT / dest).stat().st_size)


if __name__ == "__main__":
    make(sys.argv[1], "NotoSans-Regular.ttf", {"wght": 400, "wdth": 100})
    make(sys.argv[2], "NotoSansSC-Regular.ttf", {"wght": 400})
