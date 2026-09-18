"""Главный цикл: прочитать новое, понять, завести тикет, запланировать действия.

Здесь связывается всё остальное, но решений этот модуль не принимает: категорию
определяет классификатор, план действий — политика, выполнение — исполнитель.
Задача цикла — не потерять сообщение, не обработать одно и то же дважды и
правильно подхватить продолжение разговора в личке.

Продолжение разговора — важная часть. Человек пишет «нет доступа», бот в личке
спрашивает почту, человек присылает почту отдельным сообщением. Без склейки
это выглядело бы как две независимые заявки.
"""
from __future__ import annotations

import logging

from app.config import Mode, Settings
from app.core.classify import Classifier
from app.core.kb import KnowledgeBase
from app.core.policy import build_decision, done_reaction_action
from app.core.rules import classify_by_rules, extract_entities, missing_entities
from app.domain import (
    SAFE_KINDS,
    ActionStatus,
    Category,
    ChatInfo,
    Decision,
    IncomingMessage,
    Resolution,
    TicketStatus,
)
from app.store import Store
from app.transports.base import MaxTransport

log = logging.getLogger(__name__)

#: Статусы, при которых новое сообщение человека — продолжение старой заявки.
FOLLOW_UP_STATUSES = {
    TicketStatus.new.value,
    TicketStatus.clarifying.value,
    TicketStatus.waiting_user_confirm.value,
    TicketStatus.acknowledged.value,
    TicketStatus.in_progress.value,
}


class Pipeline:
    """Один проход по чатам: от входящего сообщения до запланированных действий."""

    def __init__(self, store: Store, transport: MaxTransport, classifier: Classifier,
                 kb: KnowledgeBase, settings: Settings) -> None:
        self.store = store
        self.transport = transport
        self.classifier = classifier
        self.kb = kb
        self.settings = settings
        self._own_names = {"бот", "dezhurny"}

    # ------------------------------------------------------------------ цикл
    def poll_once(self, per_chat_limit: int = 30) -> dict[str, int]:
        """Читает все рабочие чаты. Возвращает сводку: сколько нового и тикетов."""
        summary = {"chats": 0, "new_messages": 0, "tickets": 0, "actions": 0,
                   "skipped_muted": 0}
        watch = {t.strip().casefold() for t in self.settings.watch_chats}

        for chat in self.transport.list_chats():
            if self.settings.max_ignore_muted and chat.is_muted:
                # Приглушённый чат считается нерабочим: не читаем вообще.
                summary["skipped_muted"] += 1
                continue
            is_request_channel = chat.title.strip().casefold() in watch
            chat_id = self.store.upsert_chat(chat, is_request_channel=is_request_channel)
            summary["chats"] += 1

            for msg in self.transport.fetch_new_messages(chat, limit=per_chat_limit):
                if self._is_own(msg):
                    continue
                message_id = self.store.save_message(chat_id, msg)
                if message_id is None:
                    continue  # уже видели
                summary["new_messages"] += 1
                created, planned = self.process(
                    chat_id, chat, msg, message_id, is_request_channel)
                summary["tickets"] += int(created)
                summary["actions"] += planned
        return summary

    def _is_own(self, msg: IncomingMessage) -> bool:
        """Собственные сообщения бот не обрабатывает — иначе отвечал бы себе."""
        return msg.author_name.strip().casefold() in self._own_names

    # -------------------------------------------------------------- обработка
    def process(self, chat_id: int, chat: ChatInfo, msg: IncomingMessage,
                message_id: int, is_request_channel: bool) -> tuple[bool, int]:
        """Обрабатывает одно новое сообщение. Возвращает (создан тикет, действий)."""
        existing = self.store.open_ticket_for(chat_id, msg.author_name)
        if existing is not None and existing["status"] in FOLLOW_UP_STATUSES:
            planned = self._handle_follow_up(existing, chat, msg, message_id)
            if planned is not None:
                return False, planned

        verdict = self.classifier.classify(msg.text)
        decision = build_decision(
            msg, verdict, self.kb,
            is_request_channel=is_request_channel,
            escalation_contact=self.settings.escalation_contact,
            reaction_seen=self.settings.max_reaction_seen,
            reaction_done=self.settings.max_reaction_done,
        )

        # Объявления и благодарности тикетом не становятся: это шум, а не работа.
        if decision.category in (Category.info_broadcast, Category.smalltalk):
            log.debug("пропущено как не-заявка: %s", msg.text[:60])
            return False, 0

        ticket_id = self.store.create_ticket(
            chat_id=chat_id, chat_title=chat.title, msg=msg, decision=decision)
        self.store.link_message_ticket(message_id, ticket_id)
        self.store.log(ticket_id, f"Заявка принята из «{chat.title}». {decision.summary}")
        if decision.blocked_on:
            self.store.set_status(ticket_id, TicketStatus.clarifying)
            self.store.log(
                ticket_id,
                f"Ждём от пользователя: {', '.join(decision.blocked_on)}")
        planned = self._persist_actions(ticket_id, decision)
        return True, planned

    def _handle_follow_up(self, ticket, chat: ChatInfo, msg: IncomingMessage,
                          message_id: int) -> int | None:
        """Продолжение разговора по уже открытой заявке.

        Возвращает число запланированных действий, либо None — если сообщение
        всё-таки стоит считать новой заявкой.
        """
        ticket_id = int(ticket["id"])
        status = ticket["status"]
        verdict = classify_by_rules(msg.text)

        # 1. Человек подтвердил, что всё работает — закрываем заявку.
        if verdict.category == Category.smalltalk and status in (
            TicketStatus.waiting_user_confirm.value, TicketStatus.clarifying.value,
            TicketStatus.in_progress.value,
        ):
            self.store.link_message_ticket(message_id, ticket_id)
            self.store.set_user_confirmed(ticket_id, True)
            self.store.log(ticket_id, f"Пользователь подтвердил: «{msg.text[:80]}»")
            # Реакция «закрыто» ставится на исходное сообщение заявки, а не на
            # подтверждение: закрытой помечается сама заявка в чате.
            source_chat_id = self.store.chat_external_id(int(ticket["chat_id"]))
            action = done_reaction_action(
                source_chat_id or chat.external_id,
                str(ticket["source_message_id"]), self.settings.max_reaction_done)
            self.store.add_action(
                ticket_id, action.kind, action.payload, action.rationale,
                requires_approval=self._needs_approval(action),
                status=self._initial_status(action))
            return 1

        # 2. Человек дослал недостающие данные — двигаем заявку дальше.
        new_entities = extract_entities(msg.text)
        if new_entities:
            entities = {**(ticket["entities"] if isinstance(ticket["entities"], dict)
                           else {}), **new_entities}
            if isinstance(ticket["entities"], str):
                import json
                entities = {**json.loads(ticket["entities"]), **new_entities}
            resolution = Resolution(ticket["resolution"])
            still_missing = missing_entities(resolution, entities)
            self.store.link_message_ticket(message_id, ticket_id)
            self.store.log(
                ticket_id,
                f"Получены данные: {', '.join(new_entities)} — "
                + ("можно продолжать" if not still_missing
                   else f"всё ещё не хватает: {', '.join(still_missing)}"))

            decision = Decision(
                category=Category(ticket["category"]), resolution=resolution,
                confidence=float(ticket["confidence"]),
                classified_by=str(ticket["classified_by"]),
                title=str(ticket["title"]), summary=str(ticket["description"]),
                entities=entities, kb_slug=ticket["kb_slug"],
            )
            self.store.update_classification(ticket_id, decision)
            if still_missing:
                return 0

            # Планируем то, что теперь стало выполнимо, без повторной реакции.
            fresh = build_decision(
                IncomingMessage(chat=chat, external_id=msg.external_id,
                                author_name=msg.author_name, text=str(ticket["original_text"])),
                _verdict_from_ticket(ticket, entities), self.kb,
                is_request_channel=False,
                escalation_contact=self.settings.escalation_contact,
                reaction_seen=self.settings.max_reaction_seen,
                reaction_done=self.settings.max_reaction_done,
            )
            fresh.actions = [a for a in fresh.actions if a.kind not in
                             {*[k for k in SAFE_KINDS if k.value.startswith("react")]}]
            self.store.set_status(ticket_id, TicketStatus.in_progress)
            return self._persist_actions(ticket_id, fresh)

        # 3. Просто дополнение к описанию — записываем в журнал.
        self.store.link_message_ticket(message_id, ticket_id)
        self.store.log(ticket_id, f"Дополнение от пользователя: «{msg.text[:160]}»")
        return 0

    # --------------------------------------------------------------- действия
    def _persist_actions(self, ticket_id: int, decision: Decision) -> int:
        for action in decision.actions:
            self.store.add_action(
                ticket_id, action.kind, action.payload, action.rationale,
                requires_approval=self._needs_approval(action),
                status=self._initial_status(action),
            )
        return len(decision.actions)

    def _needs_approval(self, action) -> bool:
        """В shadow одобряется всё; в assisted — только безопасные действия сами."""
        if self.settings.dispatcher_mode == Mode.auto:
            return False
        if self.settings.dispatcher_mode == Mode.assisted:
            return action.kind not in SAFE_KINDS
        return action.requires_approval

    def _initial_status(self, action) -> ActionStatus:
        return (ActionStatus.approved if not self._needs_approval(action)
                else ActionStatus.proposed)


def _verdict_from_ticket(ticket, entities: dict):
    """Восстанавливает вердикт из сохранённого тикета, чтобы перепланировать действия."""
    from app.core.rules import RuleVerdict

    return RuleVerdict(
        category=Category(ticket["category"]),
        resolution=Resolution(ticket["resolution"]),
        confidence=float(ticket["confidence"]),
        kb_slug=ticket["kb_slug"],
        entities=entities,
        notes=[],
        matched=[],
    )
