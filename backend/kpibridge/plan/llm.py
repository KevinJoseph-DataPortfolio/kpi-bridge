"""Fournisseurs de modèle. V1 : Ollama local uniquement (aucun envoi externe), et un substitut de test explicitement
identifié pour la CI (`KPI_LLM_PROVIDER=fake`), jamais présenté comme une interprétation réelle."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..config import Settings
from ..errors import UserError


@dataclass
class LLMReply:
    content: str
    model: str
    duration_s: float
    provider: str


class Provider(Protocol):
    name: str

    def chat(self, system: str, user: str, schema: dict[str, Any]) -> LLMReply: ...

    def status(self) -> dict[str, Any]: ...


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Le seul hôte contacté est l'URL Ollama configurée (locale par défaut) ; pas de redirections suivies.
        self.client = httpx.Client(base_url=settings.ollama_url, timeout=settings.ollama_timeout_s, follow_redirects=False, trust_env=False)

    def status(self) -> dict[str, Any]:
        try:
            r = self.client.get("/api/tags", timeout=3)
            r.raise_for_status()
            models = [m.get("name") for m in r.json().get("models", [])]
            ok = any(m == self.settings.ollama_model or m.split(":")[0] == self.settings.ollama_model for m in models)
            return {"provider": "ollama", "reachable": True, "model": self.settings.ollama_model, "model_present": ok}
        except (httpx.HTTPError, ValueError):
            return {"provider": "ollama", "reachable": False, "model": self.settings.ollama_model, "model_present": False}

    def chat(self, system: str, user: str, schema: dict[str, Any]) -> LLMReply:
        body = {
            "model": self.settings.ollama_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": True,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": self.settings.ollama_num_ctx, "num_predict": 1800, "seed": 7},
        }
        t0 = time.monotonic()
        deadline = t0 + self.settings.ollama_timeout_s  # échéance globale réelle (pas seulement un délai de lecture)
        parts: list[str] = []
        model = self.settings.ollama_model
        try:
            with self.client.stream("POST", "/api/chat", json=body) as r:
                if r.status_code == 404:
                    raise UserError("ai_model_missing", model=self.settings.ollama_model)
                if r.status_code >= 400:
                    raise UserError("ai_error", status=r.status_code)
                for line in r.iter_lines():
                    if time.monotonic() > deadline:
                        raise UserError("ai_timeout", seconds=self.settings.ollama_timeout_s)
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise UserError("ai_error", status=500)
                    model = chunk.get("model", model)
                    parts.append((chunk.get("message") or {}).get("content") or "")
                    if chunk.get("done"):
                        break
        except httpx.TimeoutException as exc:
            raise UserError("ai_timeout", seconds=self.settings.ollama_timeout_s) from exc
        except httpx.HTTPError as exc:
            raise UserError("ai_unreachable") from exc
        content = re.sub(r"<think>.*?</think>", "", "".join(parts), flags=re.S).strip()
        return LLMReply(content=content, model=model, duration_s=time.monotonic() - t0, provider="ollama")


class FakeProvider:
    """Substitut déterministe POUR LES TESTS CI UNIQUEMENT. Il construit une réponse à partir de mots-clés et des
    rôles de colonnes. Les résultats sont marqués `provider=fake-test-double` dans le plan."""

    name = "fake-test-double"

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "reachable": True, "model": "fake", "model_present": True, "test_double": True}

    def chat(self, system: str, user: str, schema: dict[str, Any]) -> LLMReply:
        payload = json.loads(user.split("DATA_JSON:", 1)[1])
        cols = payload["columns"]
        req = payload["request"].lower()

        def role(r: str) -> str | None:
            return next((c["id"] for c in cols if r in c.get("roles", [])), None)

        amount, date, order, product = role("amount"), role("date"), role("order_id"), role("product")
        metrics: list[dict[str, Any]] = []
        charts: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        if amount:
            metrics.append({"id": "total_sales", "label": "Total", "unit": "currency", "kind": "aggregate", "a": {"fn": "sum", "field": amount}, "compare": "previous_period" if date else "none"})
            if date:
                charts.append({"id": "sales_by_period", "type": "line", "metric": "total_sales", "dimension": "__period__"})
            if order:
                metrics.append({"id": "avg_basket", "label": "Panier moyen", "unit": "currency", "kind": "ratio",
                                "a": {"fn": "sum", "field": amount}, "b": {"fn": "count_distinct", "field": order}})
            if product:
                charts.append({"id": "top_products", "type": "hbar", "metric": "total_sales", "dimension": product, "top_n": 5})
        if any(w in req for w in ("marge", "margin", "маржа", "毛利", "marge", "margen")) and not role("cost"):
            unavailable.append({"what": "margin", "missing": ["cost"]})
        if not metrics:
            metrics.append({"id": "row_count", "label": "Lignes", "unit": "count", "kind": "aggregate", "a": {"fn": "count_rows"}})
        out = {"understood": True, "metrics": metrics, "charts": charts, "period_field": date, "time_grain": "month",
               "filter_dimensions": [c for c in (product,) if c], "unavailable": unavailable}
        return LLMReply(content=json.dumps(out), model="fake", duration_s=0.0, provider=self.name)


def get_provider(settings: Settings) -> Provider:
    if settings.llm_provider == "fake":
        return FakeProvider()
    return OllamaProvider(settings)
