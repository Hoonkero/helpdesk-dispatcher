"""Контракт транспорта MAX.

Всё, что знает о конкретном способе связи с MAX (Playwright, внутренний API,
заглушка для тестов), живёт за этим интерфейсом. Ядро приложения не должно
знать, как именно ставится реакция — только что её можно поставить.

Правило безопасности зашито в тип: `send_message` принимает только личные
диалоги. Попытка написать в чат заявок обязана поднимать WriteForbidden.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain import ChatInfo, IncomingMessage


class TransportError(RuntimeError):
    """Транспорт не смог выполнить операцию (сеть, UI изменился, нет сессии)."""


class WriteForbidden(TransportError):
    """Попытка написать туда, где боту писать запрещено. Никогда не подавлять."""


class NotAuthenticated(TransportError):
    """Сессия MAX отсутствует или истекла — нужен вход человеком."""


class MaxTransport(ABC):
    """Минимальный набор операций, которого хватает всему приложению."""

    @abstractmethod
    def start(self) -> None:
        """Поднять соединение/браузер. Идемпотентно."""

    @abstractmethod
    def stop(self) -> None:
        """Закрыть соединение. Идемпотентно."""

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Есть ли живая сессия MAX."""

    @abstractmethod
    def list_chats(self) -> list[ChatInfo]:
        """Список чатов с флагами is_group и is_muted.

        Приглушённые чаты обязаны приходить с is_muted=True — на этом флаге
        построено правило «приглушённое не рабочее».
        """

    @abstractmethod
    def fetch_new_messages(self, chat: ChatInfo, limit: int = 30) -> list[IncomingMessage]:
        """Последние сообщения чата, от старых к новым.

        Дедупликацию делает вызывающий по external_id, транспорт может
        возвращать уже виденные сообщения.
        """

    @abstractmethod
    def add_reaction(self, chat: ChatInfo, message_external_id: str, emoji: str) -> None:
        """Поставить реакцию на сообщение. Повторная установка — не ошибка."""

    @abstractmethod
    def send_message(self, chat: ChatInfo, text: str, reply_to: str | None = None) -> str:
        """Отправить сообщение в ЛИЧНЫЙ диалог. Возвращает external_id отправленного.

        Обязана бросать WriteForbidden, если chat.is_group истинно.
        """

    # ------------------------------------------------------------------ утилиты
    def working_chats(self, *, ignore_muted: bool = True) -> list[ChatInfo]:
        """Рабочие чаты: приглушённые отфильтрованы, если так настроено."""
        chats = self.list_chats()
        return [c for c in chats if not (ignore_muted and c.is_muted)]

    def __enter__(self) -> "MaxTransport":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
