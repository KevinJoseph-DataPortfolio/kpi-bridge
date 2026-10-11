"""Protections HTTP de l'application locale.

Modèle : application mono-utilisateur sur 127.0.0.1. Menaces visées : page web tierce ouverte dans le navigateur
(CSRF, lecture cross-origin, rebinding DNS), autre utilisateur/processus local sans le secret de session, contenu
de fichier malveillant (XSS). Voir SECURITY.md.

Session :
1. le lanceur exécute `python -m kpibridge.session new` : un secret aléatoire à usage unique est généré, seule son
   empreinte SHA-256 est écrite dans `run/bootstrap/` (permissions restrictives), le secret est transmis au navigateur
   dans le fragment d'URL (`#token=…`, jamais envoyé au serveur ni journalisé) ;
2. la page l'échange immédiatement via POST contre un cookie HttpOnly/SameSite=Strict, puis efface le fragment ;
3. l'empreinte est supprimée : le secret ne peut plus resservir. Expiration : 5 minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .config import Settings
from .db import Database, now

COOKIE = "kpi_session"
CSRF_HEADER = "x-kpi-csrf"
PUBLIC_API = {"/api/v1/session/bootstrap", "/api/v1/health", "/api/v1/session"}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'; worker-src 'self' blob:"
)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bootstrap_dir(settings: Settings) -> Path:
    d = settings.run_dir / "bootstrap"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def create_bootstrap_token(settings: Settings) -> str:
    token = secrets.token_urlsafe(32)
    path = bootstrap_dir(settings) / f"{_hash(token)}.pending"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(str(time.time()))
    return token


def consume_bootstrap_token(settings: Settings, token: str) -> bool:
    if not token or len(token) > 200:
        return False
    d = bootstrap_dir(settings)
    # purge des jetons expirés
    for p in d.glob("*.pending"):
        try:
            if time.time() - float(p.read_text()) > settings.bootstrap_ttl_s:
                p.unlink(missing_ok=True)
        except (OSError, ValueError):
            p.unlink(missing_ok=True)
    path = d / f"{_hash(token)}.pending"
    try:
        created = float(path.read_text())
    except (OSError, ValueError):
        return False
    path.unlink(missing_ok=True)  # usage unique, même en cas d'expiration
    return time.time() - created <= settings.bootstrap_ttl_s


def create_session(db: Database, settings: Settings) -> str:
    token = secrets.token_urlsafe(32)
    t = now()
    db.run("DELETE FROM sessions WHERE expires_at < ?", (t,))
    db.run("INSERT INTO sessions (token_hash, created_at, expires_at) VALUES (?, ?, ?)", (_hash(token), t, t + settings.session_ttl_s))
    return token


def session_valid(db: Database, token: str | None) -> bool:
    if not token or len(token) > 200:
        return False
    row = db.one("SELECT expires_at FROM sessions WHERE token_hash=?", (_hash(token),))
    return bool(row and row["expires_at"] > now())


def revoke_session(db: Database, token: str | None) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))


def set_session_cookie(resp: Response, token: str, settings: Settings) -> None:
    resp.set_cookie(COOKIE, token, max_age=settings.session_ttl_s, httponly=True, samesite="strict", secure=settings.secure_cookies, path="/")


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings, db: Database) -> None:
        super().__init__(app)
        self.settings = settings
        self.db = db
        self.allowed_hosts = settings.allowed_hosts()
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts} | {f"https://{h}" for h in self.allowed_hosts}

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        host = (request.headers.get("host") or "").lower()
        if host not in self.allowed_hosts:
            return JSONResponse({"error": {"code": "forbidden_host", "params": {}}}, status_code=403)
        path = request.url.path
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        if path.startswith("/api/"):
            if origin is not None and origin not in self.allowed_origins:
                return JSONResponse({"error": {"code": "forbidden_origin", "params": {}}}, status_code=403)
            if fetch_site is not None and fetch_site not in ("same-origin", "none"):
                return JSONResponse({"error": {"code": "forbidden_origin", "params": {}}}, status_code=403)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.get(CSRF_HEADER) != "1":
                    return JSONResponse({"error": {"code": "csrf_missing", "params": {}}}, status_code=403)
            if request.method == "OPTIONS":
                return Response(status_code=405)
            if path not in PUBLIC_API and not session_valid(self.db, request.cookies.get(COOKIE)):
                return JSONResponse({"error": {"code": "session_required", "params": {}}}, status_code=401)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
