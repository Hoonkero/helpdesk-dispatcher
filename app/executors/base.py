"""Общий результат любого действия во внешнем мире."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ActionResult:
    """Что получилось. `dry_run` означает: действие только показано, не сделано."""

    ok: bool
    output: str = ""
    error: str = ""
    seconds: float = 0.0
    dry_run: bool = False

    @classmethod
    def blocked(cls, reason: str) -> "ActionResult":
        """Действие не выполнено предохранителем — это не ошибка, а штатный режим."""
        return cls(ok=True, output=reason, dry_run=True)

    @classmethod
    def failure(cls, error: str) -> "ActionResult":
        return cls(ok=False, error=error)
