"""Исполнитель одобренных действий — единственное место, где бот меняет мир.

Всё, что бот делает наружу, проходит здесь, и здесь же стоят все проверки:

* глобальный предохранитель `safe_mode` — пока он включён, наружу не уходит
  ничего, действие помечается пропущенным с пояснением;
* запрет писать в групповые чаты — повторно, на случай ошибки в политике;
* часовой лимит внешних действий — защита аккаунта от блокировки;
* каждый результат пишется в журнал тикета, чтобы было видно, что бот делал.
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.domain import ActionKind, ActionStatus, ChatInfo, TicketStatus
from app.executors.aspia import AspiaExecutor
from app.executors.base import ActionResult
from app.executors.escalate import EscalationExecutor
from app.store import Store
from app.transports.base import MaxTransport, TransportError, WriteForbidden

log = logging.getLogger(__name__)

#: После какого действия тикет переходит в какой статус.
STATUS_AFTER = {
    ActionKind.react_seen: TicketStatus.acknowledged,
    ActionKind.send_dm: TicketStatus.clarifying,
    ActionKind.aspia_script: TicketStatus.waiting_user_confirm,
    ActionKind.forward_email: TicketStatus.waiting_third_party,
    ActionKind.react_done: TicketStatus.resolved,
}


class ActionRunner:
    """Забирает одобренные действия из хранилища и выполняет их."""

    def __init__(self, store: Store, transport: MaxTransport, settings: Settings) -> None:
        self.store = store
        self.transport = transport
        self.settings = settings
        self.aspia = AspiaExecutor(
            settings.aspia_client_path,
            dry_run=settings.aspia_dry_run or settings.safe_mode,
        )
        self.escalation = EscalationExecutor(
            transport, dry_run=settings.safe_mode
        )

    # ----------------------------------------------------------------- цикл
    def run_pending(self, limit: int = 20) -> list[tuple[int, ActionResult]]:
        """Выполняет одобренные действия. Возвращает пары (id действия, результат)."""
        results: list[tuple[int, ActionResult]] = []
        for row in self.store.approved_actions(limit=limit):
            result = self.execute(int(row["id"]))
            results.append((int(row["id"]), result))
        return results

    def execute(self, action_id: int) -> ActionResult:
        """Выполняет одно действие и фиксирует результат."""
        row = self.store.action(action_id)
        if row is None:
            return ActionResult.failure(f"действия {action_id} нет")
        kind = ActionKind(row["kind"])
        payload = row["payload"]
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)
        ticket_id = int(row["ticket_id"])

        # Заметка ничего не меняет наружу — выполняется всегда.
        if kind == ActionKind.note:
            self.store.set_action_status(action_id, ActionStatus.done, "заметка")
            self.store.log(ticket_id, payload.get("reason") or "заметка")
            return ActionResult(ok=True, output="заметка")

        blocked = self._guard(kind)
        if blocked is not None:
            self.store.set_action_status(action_id, ActionStatus.skipped, blocked.output)
            self.store.log(ticket_id, f"Не выполнено: {blocked.output}")
            return blocked

        try:
            result = self._dispatch(kind, payload)
        except WriteForbidden as e:
            result = ActionResult.failure(f"запрещено: {e}")
        except TransportError as e:
            result = ActionResult.failure(f"транспорт: {e}")
        except Exception as e:  # noqa: BLE001 — падение бота хуже записанной ошибки
            log.exception("действие %s упало", action_id)
            result = ActionResult.failure(f"непредвиденная ошибка: {e}")

        status = ActionStatus.done if result.ok else ActionStatus.failed
        self.store.set_action_status(action_id, status, result.output or result.error)
        self.store.log(
            ticket_id,
            f"{kind.value}: {'выполнено' if result.ok else 'ошибка'}"
            f"{' (сухой прогон)' if result.dry_run else ''} — "
            f"{(result.output or result.error)[:200]}",
        )
        if result.ok and not result.dry_run and kind in STATUS_AFTER:
            self.store.set_status(ticket_id, STATUS_AFTER[kind])
        if result.ok and not result.dry_run:
            self.store.note_external_action(kind.value)
        return result

    # ------------------------------------------------------------- проверки
    def _guard(self, kind: ActionKind) -> ActionResult | None:
        """Возвращает результат-заглушку, если действие выпускать нельзя."""
        if self.settings.safe_mode:
            return ActionResult.blocked(
                "включён предохранитель SAFE_MODE: наружу ничего не отправляется")
        if self.store.actions_last_hour() >= self.settings.max_max_actions_per_hour:
            return ActionResult.blocked(
                f"достигнут часовой лимит внешних действий "
                f"({self.settings.max_max_actions_per_hour}) — защита аккаунта")
        return None

    # --------------------------------------------------------------- работа
    def _dispatch(self, kind: ActionKind, payload: dict) -> ActionResult:
        if kind in (ActionKind.react_seen, ActionKind.react_done):
            return self._react(payload)
        if kind == ActionKind.send_dm:
            return self._send_dm(payload)
        if kind == ActionKind.aspia_script:
            return self.aspia.run(
                payload.get("script", ""), payload.get("access_code", ""),
                payload.get("params") or {})
        if kind == ActionKind.forward_email:
            return self.escalation.forward(payload.get("to", ""), payload.get("text", ""))
        return ActionResult.failure(f"неизвестный тип действия {kind}")

    def _chat_by_id(self, external_id: str) -> ChatInfo | None:
        for chat in self.transport.list_chats():
            if chat.external_id == external_id:
                return chat
        return None

    def _react(self, payload: dict) -> ActionResult:
        chat = self._chat_by_id(payload.get("chat_external_id", ""))
        if chat is None:
            return ActionResult.failure("чат не найден — реакцию поставить некуда")
        self.transport.add_reaction(chat, payload.get("message_external_id", ""),
                                    payload.get("emoji", ""))
        return ActionResult(ok=True, output=f"реакция {payload.get('emoji')} в «{chat.title}»")

    def _send_dm(self, payload: dict) -> ActionResult:
        """Отправляет сообщение в личку. В групповой чат не отправит никогда."""
        name = (payload.get("to_name") or "").strip()
        chat = None
        if payload.get("chat_external_id"):
            chat = self._chat_by_id(payload["chat_external_id"])
        if chat is None and name:
            target = name.casefold()
            chat = next((c for c in self.transport.list_chats()
                         if not c.is_group and c.title.strip().casefold() == target), None)
        if chat is None:
            return ActionResult.failure(
                f"не найден личный диалог с «{name}» — написать некуда. "
                "Нужно, чтобы человек написал боту первым.")
        if chat.is_group:
            raise WriteForbidden(f"«{chat.title}» — групповой чат")
        if not self.store.is_write_allowed(chat.external_id):
            raise WriteForbidden(f"в «{chat.title}» запись запрещена настройками")
        self.transport.send_message(chat, payload.get("text", ""))
        return ActionResult(ok=True, output=f"сообщение в «{chat.title}»")
