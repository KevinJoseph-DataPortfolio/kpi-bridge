"""Format allow-list and real-content check (the declared MIME type is never used)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from ..config import Limits
from ..errors import LimitExceeded, UserError

SUPPORTED = {
    "csv": "csv",
    "tsv": "tsv",
    "txt": "txt",
    "xlsx": "xlsx",
    "xls": "xls",
    "pdf": "pdf",
    "docx": "docx",
    "json": "json",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
}

# Explicit refusals with a dedicated reason
MACRO_EXTS = {"xlsm", "xltm", "xlsb", "docm", "dotm", "pptm", "xlam"}
ARCHIVE_EXTS = {"zip", "7z", "rar", "gz", "tgz", "tar", "bz2", "xz", "zst", "cab", "iso"}
EXECUTABLE_EXTS = {"exe", "dll", "msi", "bat", "cmd", "ps1", "sh", "com", "scr", "js", "vbs", "jar", "app", "apk", "py"}

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
VBA_MARKERS = (
    "_VBA_PROJECT".encode("utf-16-le"),
    b"vbaProject.bin",
    "Macros".encode("utf-16-le"),
)


def extension_of(filename: str) -> str:
    name = filename.strip().lower()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1]


def check_extension(filename: str) -> str:
    ext = extension_of(filename)
    if ext in MACRO_EXTS:
        raise UserError("macro_file_refused", ext=ext)
    if ext in ARCHIVE_EXTS:
        raise UserError("archive_refused", ext=ext)
    if ext in EXECUTABLE_EXTS:
        raise UserError("executable_refused", ext=ext)
    if ext not in SUPPORTED:
        raise UserError("unsupported_format", ext=ext or "?")
    return ext


def check_zip(path: Path, limits: Limits, required: tuple[str, ...]) -> None:
    """Validates an OOXML container BEFORE extraction: paths, entry count, size and decompression ratio."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > limits.max_zip_entries:
                raise LimitExceeded("zip_too_many_entries", count=len(infos), limit=limits.max_zip_entries)
            total = 0
            names = set()
            for info in infos:
                name = info.filename
                if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/") or ":" in name:
                    raise UserError("zip_dangerous_path")
                if info.flag_bits & 0x1:
                    raise UserError("protected_file")
                total += info.file_size
                if info.compress_size > 0 and info.file_size / info.compress_size > limits.max_zip_ratio and info.file_size > 1_000_000:
                    raise LimitExceeded("zip_bomb_suspected")
                if total > limits.max_unzipped_bytes:
                    raise LimitExceeded("zip_too_large", limit_mib=limits.max_unzipped_bytes // (1024 * 1024))
                names.add(name)
            lowered = {n.lower() for n in names}
            if any(n.endswith("vbaproject.bin") for n in lowered):
                raise UserError("macro_file_refused", ext="vba")
            for req in required:
                if req.lower() not in lowered:
                    raise UserError("content_mismatch")
    except zipfile.BadZipFile as exc:
        raise UserError("corrupted_file") from exc


def sniff_format(path: Path, ext: str, limits: Limits) -> str:
    """Checks that the content matches the extension. Returns the logical format."""
    with open(path, "rb") as fh:
        head = fh.read(65536)
    if not head:
        raise UserError("empty_file")
    kind = SUPPORTED[ext]
    if kind == "xlsx":
        if head.startswith(OLE_MAGIC):
            # Encrypted OOXML = OLE container with EncryptionInfo
            raise UserError("protected_file")
        if not head.startswith(ZIP_MAGIC):
            raise UserError("content_mismatch")
        check_zip(path, limits, ("[Content_Types].xml", "xl/workbook.xml"))
        return "xlsx"
    if kind == "docx":
        if head.startswith(OLE_MAGIC):
            raise UserError("protected_file")
        if not head.startswith(ZIP_MAGIC):
            raise UserError("content_mismatch")
        check_zip(path, limits, ("[Content_Types].xml", "word/document.xml"))
        return "docx"
    if kind == "xls":
        if not head.startswith(OLE_MAGIC):
            raise UserError("content_mismatch")
        data = path.read_bytes()
        if any(m in data for m in VBA_MARKERS[:1]):
            raise UserError("macro_file_refused", ext="xls")
        return "xls"
    if kind == "pdf":
        if b"%PDF-" not in head[:1024]:
            raise UserError("content_mismatch")
        return "pdf"
    if kind == "image":
        if ext == "png" and not head.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UserError("content_mismatch")
        if ext in ("jpg", "jpeg") and not head.startswith(b"\xff\xd8\xff"):
            raise UserError("content_mismatch")
        return "image"
    # Text formats: no binary bytes (except the UTF-16 BOM)
    if head.startswith((ZIP_MAGIC, OLE_MAGIC, b"%PDF-", b"MZ", b"\x7fELF", b"\x89PNG")):
        raise UserError("content_mismatch")
    if not head.startswith((b"\xff\xfe", b"\xfe\xff")) and b"\x00" in head:
        raise UserError("content_mismatch")
    if kind == "json":
        stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
        if not stripped.startswith((b"[", b"{")):
            raise UserError("content_mismatch")
        return "json"
    return kind
