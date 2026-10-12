"""Translatable business errors: a stable code plus parameters, never a system trace shown to the user."""

from __future__ import annotations

from typing import Any


class UserError(Exception):
    """User-presentable error. The frontend translates `code` through `errors.<code>`."""

    status_code = 400

    def __init__(self, code: str, status_code: int | None = None, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = {k: v for k, v in params.items() if v is not None}
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "params": self.params}


class NotFound(UserError):
    status_code = 404

    def __init__(self, what: str = "resource") -> None:
        super().__init__("not_found", what=what)


class LimitExceeded(UserError):
    status_code = 413


class JobCancelled(Exception):
    """Raised inside a job when cancellation is requested."""
