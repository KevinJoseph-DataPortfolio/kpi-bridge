"""Configuration chargée depuis l'environnement (aucun secret fixe).

Toutes les limites sont configurables par variables d'environnement `KPI_*` et exposées à l'interface
via `/api/v1/meta` afin d'être visibles par l'utilisateur.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

MIB = 1024 * 1024


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier") from exc
    if value <= 0:
        raise ValueError(f"{name} doit être positif")
    return value


def _find_tesseract() -> str | None:
    explicit = os.environ.get("KPI_TESSERACT_CMD")
    if explicit:
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    home = Path.home() / ".kpi-bridge" / "tools" / "tesseract" / "Library" / "bin" / "tesseract.exe"
    if home.exists():
        return str(home)
    return None


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 25 * MIB
    max_rows: int = 200_000
    max_cols: int = 200
    max_pdf_pages: int = 50
    max_ocr_pages: int = 20
    max_image_pixels: int = 40_000_000
    max_unzipped_bytes: int = 200 * MIB
    max_zip_entries: int = 5_000
    max_zip_ratio: int = 150
    max_json_depth: int = 8
    max_cells: int = 8_000_000
    job_timeout_s: int = 900
    job_memory_mib: int = 3_072
    max_request_chars: int = 2_000

    def as_public(self) -> dict[str, int]:
        return {
            "max_file_mib": self.max_file_bytes // MIB,
            "max_rows": self.max_rows,
            "max_cols": self.max_cols,
            "max_pdf_pages": self.max_pdf_pages,
            "max_ocr_pages": self.max_ocr_pages,
            "max_request_chars": self.max_request_chars,
            "job_timeout_s": self.job_timeout_s,
        }


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    public_origin_hosts: tuple[str, ...] = ()
    limits: Limits = field(default_factory=Limits)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    ollama_timeout_s: int = 120
    ollama_num_ctx: int = 4096
    llm_provider: str = "ollama"  # "ollama" | "fake" (tests CI uniquement, identifié comme substitut)
    tesseract_cmd: str | None = None
    session_ttl_s: int = 12 * 3600
    bootstrap_ttl_s: int = 300
    secure_cookies: bool = False
    static_dir: Path | None = None
    worker_poll_s: float = 0.5

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kpibridge.sqlite3"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def run_dir(self) -> Path:
        return self.data_dir / "run"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def recipes_dir(self) -> Path:
        return self.data_dir / "recipes"

    def allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        hosts.update(self.public_origin_hosts)
        return hosts


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("KPI_DATA_DIR") or (Path.home() / ".kpi-bridge" / "data")).resolve()
    limits = Limits(
        max_file_bytes=_int("KPI_MAX_FILE_MIB", 25) * MIB,
        max_rows=_int("KPI_MAX_ROWS", 200_000),
        max_cols=_int("KPI_MAX_COLS", 200),
        max_pdf_pages=_int("KPI_MAX_PDF_PAGES", 50),
        max_ocr_pages=_int("KPI_MAX_OCR_PAGES", 20),
        job_timeout_s=_int("KPI_JOB_TIMEOUT_S", 900),
        job_memory_mib=_int("KPI_JOB_MEMORY_MIB", 3072),
    )
    static = os.environ.get("KPI_STATIC_DIR")
    extra_hosts = tuple(h.strip() for h in os.environ.get("KPI_EXTRA_HOSTS", "").split(",") if h.strip())
    return Settings(
        data_dir=data_dir,
        host=os.environ.get("KPI_HOST", "127.0.0.1"),
        port=_int("KPI_PORT", 8765),
        public_origin_hosts=extra_hosts,
        limits=limits,
        ollama_url=os.environ.get("KPI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.environ.get("KPI_OLLAMA_MODEL", "qwen3:4b"),
        ollama_timeout_s=_int("KPI_OLLAMA_TIMEOUT_S", 120),
        ollama_num_ctx=_int("KPI_OLLAMA_NUM_CTX", 4096),
        llm_provider=os.environ.get("KPI_LLM_PROVIDER", "ollama"),
        tesseract_cmd=_find_tesseract(),
        session_ttl_s=_int("KPI_SESSION_TTL_S", 12 * 3600),
        secure_cookies=os.environ.get("KPI_SECURE_COOKIES", "0") == "1",
        static_dir=Path(static).resolve() if static else None,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def ensure_dirs(settings: Settings) -> None:
    for d in (settings.data_dir, settings.projects_dir, settings.run_dir, settings.tmp_dir, settings.recipes_dir):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
