"""Эскалация: вежливая передача обращения ответственному коллеге.

Отдельный модуль, потому что это не техническое действие, а социальное:
пользователю нужно сказать «принято, подождите», а коллеге — передать суть и
почту, не пересказывая всю переписку.
"""
from __future__ import annotations

import logging

from app.domain import ChatInfo
from app.executors.base import ActionResult
from app.transports.base import MaxTransport, TransportError

log = logging.getLogger(__name__)


class EscalationExecutor:
    """Пересылает обращение в личку ответственному."""

    def __init__(self, transport: MaxTransport, *, dry_run: bool = True) -> None:
        self.transport = transport
        self.dry_run = dry_run

    def find_chat(self, name: str) -> ChatInfo | None:
        """Ищет диалог с ответственным по имени. Групповой чат тоже подходит:
        коллеги-исполнители сидят в своём рабочем чате, и туда писать можно."""
        target = name.strip().casefold()
        for chat in self.transport.list_chats():
            if chat.title.strip().casefold() == target:
                return chat
        return None

    def forward(self, to_name: str, text: str) -> ActionResult:
        chat = self.find_chat(to_name)
        if chat is None:
            return ActionResult.failure(
                f"не найден чат «{to_name}» — некуда передать заявку")
        if self.dry_run:
            return ActionResult.blocked(
                f"Сухой прогон: передал бы в «{chat.title}»: {text[:120]}")
        try:
            self.transport.send_message(chat, text)
        except TransportError as e:
            return ActionResult.failure(f"не удалось передать в «{chat.title}»: {e}")
        return ActionResult(ok=True, output=f"передано в «{chat.title}»")
