#!/usr/bin/env python3
"""KPI Bridge — outil de contrôle local (installation, lancement, arrêt, diagnostic, mise à jour, sauvegarde,
désinstallation). Bibliothèque standard uniquement : il s'exécute avant que l'environnement virtuel existe.

    python scripts/kpictl.py setup [--no-ai] [--no-ocr] [--model qwen3:4b]
    python scripts/kpictl.py launch [--no-browser] [--docker]
    python scripts/kpictl.py stop | status | doctor | update | backup | uninstall [--purge-data]

Principes : idempotent (rien n'est retéléchargé si présent), aucune modification système, aucun contournement de
politique d'exécution, téléchargements uniquement depuis les sources officielles avec affichage du volume attendu.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WIN = os.name == "nt"
HOME_DIR = Path(os.environ.get("KPI_HOME") or (Path.home() / ".kpi-bridge"))
TOOLS = HOME_DIR / "tools"
DATA = Path(os.environ.get("KPI_DATA_DIR") or (HOME_DIR / "data"))
RUN = DATA / "run"
LOGS = DATA / "logs"
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
PORT = int(os.environ.get("KPI_PORT", "8765"))
MODEL = os.environ.get("KPI_OLLAMA_MODEL", "qwen3:4b")
OLLAMA_URL = os.environ.get("KPI_OLLAMA_URL", "http://127.0.0.1:11434")
NODE_VERSION = "24.21.0"
OLLAMA_VERSION = "0.40.3"
TESSERACT_LANGS = ["fra", "eng", "deu", "spa", "rus", "chi_sim"]


# ----------------------------------------------------------------------------- utilitaires
def say(msg: str) -> None:
    print(f"[kpi-bridge] {msg}", flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"[kpi-bridge] ERREUR : {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def which(name: str, extra: list[Path] | None = None) -> str | None:
    for p in extra or []:
        if p.exists():
            return str(p)
    return shutil.which(name)


def human(n: float) -> str:
    for unit in ("o", "Kio", "Mio", "Gio"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} Tio"


def download(url: str, dest: Path, expected_sha256: str | None = None) -> Path:
    """Téléchargement avec progression réelle et reprise idempotente (fichier final déjà présent = réutilisé)."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "kpi-bridge-setup"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — URL officielle codée en dur
        total = int(r.headers.get("Content-Length") or 0)
        say(f"téléchargement {url.split('/')[-1]} ({human(total) if total else 'taille inconnue'})")
        done = 0
        last = 0.0
        h = hashlib.sha256()
        with open(tmp, "wb") as out:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if time.time() - last > 1:
                    pct = f"{done * 100 / total:.0f} %" if total else human(done)
                    print(f"\r    {pct} ({human(done)})", end="", flush=True)
                    last = time.time()
        print()
    if expected_sha256 and h.hexdigest() != expected_sha256.lower():
        tmp.unlink(missing_ok=True)
        fail(f"empreinte SHA-256 inattendue pour {url}")
    tmp.replace(dest)
    return dest


def http_ok(url: str, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status == 200
    except OSError:
        return False


def env_for_app() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("KPI_DATA_DIR", str(DATA))
    env.setdefault("KPI_PORT", str(PORT))
    env["KPI_STATIC_DIR"] = str(ROOT / "frontend" / "dist")
    env["KPI_LOCALES_DIR"] = str(ROOT / "shared" / "locales")
    env["PYTHONPATH"] = str(ROOT / "backend")
    env["PYTHONIOENCODING"] = "utf-8"
    tess = tesseract_path()
    if tess:
        env.setdefault("KPI_TESSERACT_CMD", tess)
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


# ----------------------------------------------------------------------------- outils externes
def ollama_path() -> str | None:
    return which("ollama", [TOOLS / "ollama" / ("ollama.exe" if IS_WIN else "bin/ollama")])


def tesseract_path() -> str | None:
    return which("tesseract", [TOOLS / "tesseract" / "Library" / "bin" / "tesseract.exe", TOOLS / "tesseract" / "bin" / "tesseract"])


def node_path() -> str | None:
    name = "node.exe" if IS_WIN else "bin/node"
    return which("node", [TOOLS / "node" / name])


def ensure_python() -> None:
    if sys.version_info < (3, 12):
        fail(f"Python 3.12 ou plus est requis (trouvé {platform.python_version()}). Installez-le depuis https://www.python.org/downloads/")


def ensure_venv() -> None:
    lock = ROOT / "backend" / "requirements.lock"
    stamp = VENV / ".kpi-lock-sha256"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    if PY.exists() and stamp.exists() and stamp.read_text().strip() == digest:
        say("environnement Python déjà à jour")
        return
    if not PY.exists():
        say("création de l'environnement Python (.venv)")
        run([sys.executable, "-m", "venv", str(VENV)])
    say("installation des dépendances verrouillées (empreintes vérifiées)")
    run([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--require-hashes", "-r", str(lock)])
    # le code de l'application est exécuté depuis backend/ via PYTHONPATH (pas d'installation de paquet nécessaire)
    stamp.write_text(digest)


def ensure_node() -> str:
    node = node_path()
    if node:
        return node
    system, machine = platform.system(), platform.machine().lower()
    arch = "x64" if machine in ("amd64", "x86_64") else ("arm64" if machine in ("arm64", "aarch64") else machine)
    if system == "Windows":
        name, ext = f"node-v{NODE_VERSION}-win-{arch}", "zip"
    elif system == "Darwin":
        name, ext = f"node-v{NODE_VERSION}-darwin-{arch}", "tar.gz"
    else:
        name, ext = f"node-v{NODE_VERSION}-linux-{arch}", "tar.gz"
    base = f"https://nodejs.org/dist/v{NODE_VERSION}"
    sums = download(f"{base}/SHASUMS256.txt", TOOLS / "downloads" / f"node-{NODE_VERSION}-SHASUMS256.txt").read_text()
    expected = next((ln.split()[0] for ln in sums.splitlines() if ln.endswith(f"{name}.{ext}")), None)
    archive = download(f"{base}/{name}.{ext}", TOOLS / "downloads" / f"{name}.{ext}", expected)
    target = TOOLS / "node"
    if target.exists():
        shutil.rmtree(target)
    tmp = TOOLS / "node-extract"
    shutil.rmtree(tmp, ignore_errors=True)
    if ext == "zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)  # noqa: S202 — archive officielle vérifiée (SHA-256) ; zipfile neutralise chemins absolus et ".."
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp, filter="data")
    (tmp / name).rename(target)
    shutil.rmtree(tmp, ignore_errors=True)
    return node_path() or fail("Node introuvable après installation")  # type: ignore[return-value]


def ensure_frontend() -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if dist.exists():
        say("interface déjà compilée (frontend/dist)")
        return
    node = ensure_node()
    node_dir = Path(node).parent
    npm = node_dir / ("npm.cmd" if IS_WIN else "npm")
    env = dict(os.environ)
    env["PATH"] = str(node_dir) + os.pathsep + env.get("PATH", "")
    say("compilation de l'interface (npm ci + build)")
    run([str(npm), "ci", "--no-fund", "--no-audit"], cwd=ROOT / "frontend", env=env)
    run([str(npm), "run", "build"], cwd=ROOT / "frontend", env=env)


def ensure_tesseract() -> None:
    if tesseract_path():
        say(f"OCR Tesseract présent : {tesseract_path()}")
        return
    if not IS_WIN:
        say("Tesseract absent. Installation système requise (droits administrateur) :")
        say("  Debian/Ubuntu : sudo apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-rus tesseract-ocr-chi-sim tesseract-ocr-deu tesseract-ocr-spa")
        say("  macOS        : brew install tesseract tesseract-lang")
        say("L'application fonctionne sans OCR (PDF scannés et images refusés avec un message clair).")
        return
    say("installation de Tesseract en mode utilisateur via micromamba (conda-forge), sans droits administrateur")
    mm = download("https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64", TOOLS / "micromamba.exe")
    run([str(mm), "create", "-y", "-r", str(HOME_DIR / "mamba"), "-p", str(TOOLS / "tesseract"), "-c", "conda-forge", "--override-channels", "tesseract"],
        stdout=subprocess.DEVNULL)
    tessdata = TOOLS / "tesseract" / "share" / "tessdata"
    for lang in TESSERACT_LANGS:
        target = tessdata / f"{lang}.traineddata"
        if not target.exists():
            download(f"https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata", target)
    say("Tesseract installé")


def ensure_ollama(model: str) -> None:
    exe = ollama_path()
    if not exe:
        if IS_WIN:
            say(f"installation d'Ollama {OLLAMA_VERSION} (archive officielle ≈ 1,4 Gio, mode utilisateur)")
            arch = download(f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-windows-amd64.zip", TOOLS / "downloads" / f"ollama-{OLLAMA_VERSION}.zip")
            with zipfile.ZipFile(arch) as zf:
                zf.extractall(TOOLS / "ollama")  # noqa: S202 — archive officielle Ollama ; zipfile neutralise chemins absolus et ".."
            exe = ollama_path()
        else:
            say("Ollama absent. Installez-le depuis https://ollama.com/download (le script officiel Linux demande sudo).")
            say("L'application fonctionne sans IA : suggestions et constructeur guidé restent disponibles.")
            return
    start_ollama()
    if model_present(model):
        say(f"modèle {model} déjà présent")
        return
    say(f"téléchargement du modèle {model} (≈ 2,5 Gio pour qwen3:4b) via Ollama")
    pull_model(model)


def start_ollama() -> None:
    if http_ok(f"{OLLAMA_URL}/api/tags"):
        return
    exe = ollama_path()
    if not exe:
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["OLLAMA_HOST"] = OLLAMA_URL.replace("http://", "")  # écoute locale uniquement
    env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
    env.setdefault("OLLAMA_NUM_PARALLEL", "1")
    spawn([exe, "serve"], "ollama", env)
    for _ in range(60):
        if http_ok(f"{OLLAMA_URL}/api/tags"):
            return
        time.sleep(0.5)
    say("Ollama ne répond pas (voir data/logs/ollama.log)")


def model_present(model: str) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:  # noqa: S310
            names = [m.get("name") for m in json.load(r).get("models", [])]
        return model in names or f"{model}:latest" in names
    except OSError:
        return False


def pull_model(model: str) -> None:
    req = urllib.request.Request(f"{OLLAMA_URL}/api/pull", data=json.dumps({"model": model, "stream": True}).encode(), headers={"Content-Type": "application/json"})
    last = 0.0
    with urllib.request.urlopen(req, timeout=3600) as r:  # noqa: S310
        for line in r:
            ev = json.loads(line)
            if "error" in ev:
                fail(f"téléchargement du modèle : {ev['error']}")
            if ev.get("total") and time.time() - last > 1:
                print(f"\r    {ev.get('status', '')} {ev.get('completed', 0) * 100 // ev['total']} % de {human(ev['total'])}", end="", flush=True)
                last = time.time()
    print()
    say(f"modèle {model} prêt")


def ensure_env_file() -> None:
    target = ROOT / ".env"
    if not target.exists():
        shutil.copyfile(ROOT / ".env.example", target)
        say(".env créé depuis .env.example (aucun secret)")


# ----------------------------------------------------------------------------- processus
def pidfile(name: str) -> Path:
    return RUN / f"{name}.pid"


def spawn(cmd: list[str], name: str, env: dict[str, str]) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{name}.log", "ab")  # noqa: SIM115
    kwargs: dict = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": env, "cwd": str(ROOT)}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    p = subprocess.Popen(cmd, **kwargs)  # noqa: S603
    pidfile(name).write_text(str(p.pid))
    return p.pid


def alive(pid: int) -> bool:
    if IS_WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill(name: str) -> None:
    pf = pidfile(name)
    if not pf.exists():
        return
    try:
        pid = int(pf.read_text())
    except ValueError:
        pf.unlink(missing_ok=True)
        return
    if alive(pid):
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                os.kill(pid, signal.SIGTERM)
        say(f"{name} arrêté (pid {pid})")
    pf.unlink(missing_ok=True)


def running(name: str) -> bool:
    pf = pidfile(name)
    try:
        return pf.exists() and alive(int(pf.read_text()))
    except ValueError:
        return False


# ----------------------------------------------------------------------------- commandes
def cmd_setup(args: argparse.Namespace) -> None:
    ensure_python()
    DATA.mkdir(parents=True, exist_ok=True)
    ensure_env_file()
    ensure_venv()
    ensure_frontend()
    if not args.no_ocr:
        try:
            ensure_tesseract()
        except (OSError, subprocess.CalledProcessError) as exc:
            say(f"OCR non installé ({type(exc).__name__}) : l'application reste utilisable sans OCR.")
    if not args.no_ai:
        try:
            ensure_ollama(args.model)
        except (OSError, subprocess.CalledProcessError) as exc:
            say(f"IA locale non installée ({type(exc).__name__}) : suggestions et constructeur guidé restent disponibles.")
    say("installation terminée")


def cmd_launch(args: argparse.Namespace) -> None:
    if args.docker:
        return docker_launch(args)
    if not PY.exists() or not (ROOT / "frontend" / "dist" / "index.html").exists():
        say("première exécution : installation")
        cmd_setup(argparse.Namespace(no_ai=args.no_ai, no_ocr=False, model=MODEL))
    else:
        ensure_venv()  # réinstalle seulement si le verrou a changé
    env = env_for_app()
    if not args.no_ai:
        start_ollama()
    if not running("worker"):
        spawn([str(PY), "-m", "kpibridge.worker"], "worker", env)
        say("worker démarré")
    if not running("app"):
        spawn([str(PY), "-m", "kpibridge.app"], "app", env)
        say(f"application démarrée sur http://127.0.0.1:{PORT}")
    for _ in range(120):
        if http_ok(f"http://127.0.0.1:{PORT}/api/v1/health"):
            break
        time.sleep(0.5)
    else:
        fail("l'application ne répond pas (voir les journaux dans " + str(LOGS) + ")")
    url = subprocess.run([str(PY), "-m", "kpibridge.session", "new"], env=env, capture_output=True, text=True, check=True).stdout.strip()
    if args.no_browser:
        say("ouvrez ce lien à usage unique dans votre navigateur (valable 5 minutes) :")
        print(url)
    else:
        webbrowser.open(url)
        say("navigateur ouvert (lien à usage unique, non conservé)")


def docker_launch(args: argparse.Namespace) -> None:
    docker = shutil.which("docker")
    if not docker:
        fail("Docker n'est pas installé. Voir README (installation Docker Desktop : droits administrateur et virtualisation requis).")
    profile = ["-f", "docker-compose.yml", "-f", "docker-compose.gpu.yml"] if args.gpu else []
    run([docker, "compose", *profile, "up", "-d", "--build"], cwd=ROOT)
    for _ in range(240):
        if http_ok(f"http://127.0.0.1:{PORT}/api/v1/health"):
            break
        time.sleep(1)
    else:
        fail("le conteneur ne répond pas : docker compose logs")
    if not args.no_ai:
        say(f"préparation du modèle {MODEL} dans le conteneur Ollama (≈ 2,5 Gio au premier lancement)")
        run([docker, "compose", "exec", "-T", "ollama", "ollama", "pull", MODEL], cwd=ROOT)
    url = subprocess.run([docker, "compose", "exec", "-T", "app", "python", "-m", "kpibridge.session", "new"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if args.no_browser:
        print(url)
    else:
        webbrowser.open(url)


def cmd_stop(_args: argparse.Namespace) -> None:
    for name in ("app", "worker", "ollama"):
        kill(name)
    say("arrêt terminé")


def cmd_status(_args: argparse.Namespace) -> None:
    for name in ("app", "worker", "ollama"):
        say(f"{name:7s} : {'actif' if running(name) else 'arrêté'}")
    say(f"santé API : {'ok' if http_ok(f'http://127.0.0.1:{PORT}/api/v1/health') else 'injoignable'}")


def cmd_doctor(_args: argparse.Namespace) -> None:
    say(f"système : {platform.system()} {platform.release()} {platform.machine()}, Python {platform.python_version()}")
    try:
        total = shutil.disk_usage(DATA if DATA.exists() else HOME_DIR.parent)
        say(f"disque libre : {human(total.free)}")
    except OSError:
        pass
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        out = subprocess.run([nvidia, "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
        say(f"GPU : {out or 'non détecté'}")
    else:
        say("GPU NVIDIA : non détecté (repli CPU d'Ollama)")
    say(f"environnement Python : {'ok' if PY.exists() else 'absent (lancer setup)'}")
    say(f"interface compilée : {'ok' if (ROOT / 'frontend/dist/index.html').exists() else 'absente'}")
    say(f"Tesseract : {tesseract_path() or 'absent (OCR indisponible)'}")
    say(f"Ollama : {ollama_path() or 'absent (IA indisponible)'} ; service : {'ok' if http_ok(OLLAMA_URL + '/api/tags') else 'arrêté'} ; modèle {MODEL} : {'présent' if model_present(MODEL) else 'absent'}")
    say(f"données : {DATA}")
    cmd_status(_args)


def cmd_update(args: argparse.Namespace) -> None:
    if (ROOT / ".git").exists() and shutil.which("git"):
        run(["git", "pull", "--ff-only"], cwd=ROOT)
    shutil.rmtree(ROOT / "frontend" / "dist", ignore_errors=True)
    cmd_setup(argparse.Namespace(no_ai=args.no_ai, no_ocr=False, model=MODEL))


def cmd_backup(_args: argparse.Namespace) -> None:
    dest_dir = HOME_DIR / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"kpi-bridge-data-{stamp}.zip"
    db = DATA / "kpibridge.sqlite3"
    snapshot = dest_dir / f"kpibridge-{stamp}.sqlite3"
    if db.exists():
        src = sqlite3.connect(db)
        out = sqlite3.connect(snapshot)
        src.backup(out)  # copie cohérente même si l'application tourne
        out.close()
        src.close()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        if snapshot.exists():
            zf.write(snapshot, "kpibridge.sqlite3")
        for p in (DATA / "projects").rglob("*") if (DATA / "projects").exists() else []:
            if p.is_file() and "work" not in p.parts and "exports" not in p.parts:
                zf.write(p, p.relative_to(DATA))
        for p in (DATA / "recipes").rglob("*") if (DATA / "recipes").exists() else []:
            if p.is_file():
                zf.write(p, p.relative_to(DATA))
    snapshot.unlink(missing_ok=True)
    say(f"sauvegarde : {dest} ({human(dest.stat().st_size)}). Restauration : arrêter, puis décompresser dans {DATA}.")


def cmd_uninstall(args: argparse.Namespace) -> None:
    cmd_stop(args)
    shutil.rmtree(VENV, ignore_errors=True)
    shutil.rmtree(ROOT / "frontend" / "dist", ignore_errors=True)
    shutil.rmtree(ROOT / "frontend" / "node_modules", ignore_errors=True)
    say("environnement et interface compilée supprimés")
    if args.purge_tools:
        shutil.rmtree(TOOLS, ignore_errors=True)
        say(f"outils supprimés ({TOOLS}). Modèles Ollama : supprimer ~/.ollama/models si souhaité.")
    if args.purge_data:
        answer = input(f"Supprimer DÉFINITIVEMENT toutes les données ({DATA}) ? Tapez OUI : ")
        if answer.strip() == "OUI":
            shutil.rmtree(DATA, ignore_errors=True)
            say("données supprimées (les sauvegardes externes ne sont pas concernées)")
        else:
            say("données conservées")


def main() -> None:
    ap = argparse.ArgumentParser(prog="kpictl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--no-ai", action="store_true")
    s.add_argument("--no-ocr", action="store_true")
    s.add_argument("--model", default=MODEL)
    l = sub.add_parser("launch")  # noqa: E741
    l.add_argument("--no-browser", action="store_true")
    l.add_argument("--no-ai", action="store_true")
    l.add_argument("--docker", action="store_true")
    l.add_argument("--gpu", action="store_true")
    for name in ("stop", "status", "doctor", "backup"):
        sub.add_parser(name)
    u = sub.add_parser("update")
    u.add_argument("--no-ai", action="store_true")
    un = sub.add_parser("uninstall")
    un.add_argument("--purge-data", action="store_true")
    un.add_argument("--purge-tools", action="store_true")
    args = ap.parse_args()
    {"setup": cmd_setup, "launch": cmd_launch, "stop": cmd_stop, "status": cmd_status, "doctor": cmd_doctor,
     "update": cmd_update, "backup": cmd_backup, "uninstall": cmd_uninstall}[args.cmd](args)


if __name__ == "__main__":
    main()
