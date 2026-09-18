"""Доменные типы: категории заявок, решения, статусы, события.

Здесь нет ни БД, ни браузера, ни сети — только смысл предметной области.
Всё остальное (store, transports, executors) зависит от этого модуля, а не наоборот.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Category(str, Enum):
    """Категория обращения. Определяет, какую политику применять."""

    access_blocked = "access_blocked"      # учётка заблокирована / нет доступа
    password_reset = "password_reset"      # забыл или нужно сбросить пароль
    printer = "printer"                    # принтер, тонер, картридж, печать
    software_install = "software_install"   # установить/обновить программу
    cloud_access = "cloud_access"          # не заходит в Клауд и прочие сервисы
    hardware = "hardware"                  # железо, физическая поломка
    network = "network"                    # интернет, wi-fi, сеть
    howto = "howto"                        # «не понимаю, как сделать»
    info_broadcast = "info_broadcast"      # объявление, не заявка
    smalltalk = "smalltalk"                # «спасибо», «ок», болтовня
    unknown = "unknown"                    # не удалось определить


class Resolution(str, Enum):
    """Как планируется закрыть обращение."""

    auto_answer = "auto_answer"          # ответ из базы знаний в ЛС
    need_clarify = "need_clarify"        # не хватает данных, уточняем в ЛС
    remote_script = "remote_script"      # подключиться через Aspia и выполнить скрипт
    escalate_email = "escalate_email"    # переслать почту ответственному
    needs_human = "needs_human"          # нужно физическое присутствие
    ignore = "ignore"                    # реагировать не нужно


class TicketStatus(str, Enum):
    new = "new"
    acknowledged = "acknowledged"                  # поставлена реакция «увидел»
    clarifying = "clarifying"                      # ждём ответа в ЛС
    in_progress = "in_progress"
    waiting_user_confirm = "waiting_user_confirm"  # спросили «теперь всё ок?»
    waiting_third_party = "waiting_third_party"    # переслали коллеге, ждём
    resolved = "resolved"
    closed_by_human = "closed_by_human"
    cancelled = "cancelled"


class ActionKind(str, Enum):
    react_seen = "react_seen"        # реакция «увидел» (👌)
    react_done = "react_done"        # реакция «закрыто» (✔️)
    send_dm = "send_dm"              # сообщение в личку
    aspia_script = "aspia_script"    # удалённое действие через Aspia
    forward_email = "forward_email"  # пересылка почты ответственному
    note = "note"                    # запись в журнал, без внешнего эффекта


class ActionStatus(str, Enum):
    proposed = "proposed"    # бот предлагает, ждёт оператора
    approved = "approved"    # оператор одобрил, ждёт исполнения
    done = "done"
    failed = "failed"
    rejected = "rejected"
    skipped = "skipped"


#: Действия, которые меняют мир и по умолчанию требуют одобрения оператора.
EXTERNAL_KINDS = frozenset(
    {
        ActionKind.react_seen,
        ActionKind.react_done,
        ActionKind.send_dm,
        ActionKind.aspia_script,
        ActionKind.forward_email,
    }
)

#: Действия, безопасные для авторежима: ничего не пишут людям, только помечают.
SAFE_KINDS = frozenset({ActionKind.react_seen, ActionKind.note})


@dataclass(slots=True)
class ChatInfo:
    """Чат в MAX как его видит транспорт."""

    external_id: str
    title: str
    is_group: bool = True
    is_muted: bool = False
    unread: int = 0


@dataclass(slots=True)
class IncomingMessage:
    """Нормализованное входящее сообщение — единая форма для всех транспортов."""

    chat: ChatInfo
    external_id: str
    author_name: str
    text: str
    sent_at: datetime = field(default_factory=utcnow)
    author_external_id: str | None = None
    has_attachments: bool = False
    own_reactions: tuple[str, ...] = ()
    reply_to_external_id: str | None = None


@dataclass(slots=True)
class PlannedAction:
    """Действие, которое бот намерен совершить. До исполнения — только план."""

    kind: ActionKind
    payload: dict
    rationale: str
    requires_approval: bool = True


@dataclass(slots=True)
class Decision:
    """Результат анализа одного сообщения."""

    category: Category
    resolution: Resolution
    confidence: float
    classified_by: str                      # rules | llm | rules+llm
    title: str
    summary: str
    entities: dict = field(default_factory=dict)
    actions: list[PlannedAction] = field(default_factory=list)
    kb_slug: str | None = None
    #: Каких данных не хватает, чтобы выполнить `resolution`. Пока список не
    #: пуст, заявка ждёт ответа пользователя — но само решение не меняется:
    #: заблокированная учётка остаётся заблокированной учёткой, даже если мы
    #: ещё не знаем почту.
    blocked_on: list[str] = field(default_factory=list)
