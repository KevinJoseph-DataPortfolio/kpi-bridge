"""Browser-test server: throwaway data, real worker, built application on 127.0.0.1:8799.

Tests obtain a single-use session link through `python scripts/e2e_server.py token`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(tempfile.gettempdir()) / "kpi-bridge-e2e-datadir.txt"


def env(data_dir: str) -> dict[str, str]:
    e = dict(os.environ)
    e.update({
        "KPI_DATA_DIR": data_dir,
        "KPI_PORT": "8799",
        "KPI_STATIC_DIR": str(ROOT / "frontend" / "dist"),
        "KPI_LOCALES_DIR": str(ROOT / "shared" / "locales"),
        "PYTHONPATH": str(ROOT / "backend"),
        "PYTHONIOENCODING": "utf-8",
        "KPI_LLM_PROVIDER": os.environ.get("KPI_LLM_PROVIDER", "fake"),
    })
    return e


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "token":
        data_dir = STATE.read_text().strip()
        out = subprocess.run([sys.executable, "-m", "kpibridge.session", "new"], env=env(data_dir), capture_output=True, text=True, check=True)
        print(out.stdout.strip())
        return
    data_dir = tempfile.mkdtemp(prefix="kpi_e2e_")
    STATE.write_text(data_dir)
    e = env(data_dir)
    worker = subprocess.Popen([sys.executable, "-m", "kpibridge.worker"], env=e, cwd=str(ROOT))
    try:
        subprocess.run([sys.executable, "-m", "kpibridge.app"], env=e, cwd=str(ROOT), check=False)
    finally:
        worker.terminate()


if __name__ == "__main__":
    main()
