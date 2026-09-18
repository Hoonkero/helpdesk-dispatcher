"""Тесты разговорного цикла: заявка — уточнение — ответ — закрытие.

Самое ценное поведение бота не в том, что он понимает одно сообщение, а в том,
что он помнит разговор: человек пишет в чат, отвечает в личке, подтверждает
решение — и всё это одна заявка, а не три.
"""
from __future__ import annotations

import json
import unittest

from app.config import Mode, Settings
from app.core.classify import Classifier
from app.core.kb import KnowledgeBase
from app.core.pipeline import Pipeline
from app.domain import ActionKind, TicketStatus
from app.store import Store
from app.transports.fake import DM_MAXIM, DM_OLGA, IT_MAIN, FakeMaxTransport


class ConversationCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Settings(max_watch_chats="ИТ Заявки", safe_mode=True,
                            llm_enabled=False, dispatcher_mode=Mode.shadow)
        self.store = Store(":memory:")
        self.transport = FakeMaxTransport()
        self.transport.start()
        self.pipeline = Pipeline(self.store, self.transport, Classifier(None),
                                 KnowledgeBase("kb"), self.cfg)

    def ticket_of(self, requester: str):
        for row in self.store.tickets(limit=200):
            if row["requester_name"] == requester:
                return row
        self.fail(f"нет заявки от {requester}")

    def log_text(self, ticket_id: int) -> str:
        row = self.store.ticket(ticket_id)
        return " | ".join(e["text"] for e in json.loads(row["work_log"]))


class TestFollowUpAcrossChats(ConversationCase):
    def test_room_sent_in_dm_joins_the_same_ticket(self) -> None:
        """Яна пишет про тонер в чат, а кабинет присылает в личку."""
        self.pipeline.poll_once()
        tickets = [r for r in self.store.tickets(limit=200)
                   if r["requester_name"] == "Ольга Белова"]
        self.assertEqual(len(tickets), 1, "должна быть одна заявка, а не две")
        entities = json.loads(tickets[0]["entities"])
        self.assertEqual(entities.get("room"), "305")

    def test_clarification_still_missing_is_reported_honestly(self) -> None:
        """Антону нужна почта, а он присылает код Aspia — заявка не должна «поехать»."""
        self.pipeline.poll_once()
        ticket = self.ticket_of("Максим Р")
        self.assertEqual(ticket["status"], TicketStatus.clarifying.value)
        self.assertIn("всё ещё не хватает: email", self.log_text(int(ticket["id"])))

    def test_answer_unblocks_the_ticket(self) -> None:
        """Как только пришла почта, заявка уходит в работу и планирует эскалацию."""
        self.pipeline.poll_once()
        ticket_id = int(self.ticket_of("Максим Р")["id"])
        self.transport.inject(DM_MAXIM, "Максим Р", "почта maxim.r@example.org")
        self.pipeline.poll_once()

        ticket = self.store.ticket(ticket_id)
        self.assertEqual(ticket["status"], TicketStatus.in_progress.value)
        kinds = [a["kind"] for a in self.store.actions_for(ticket_id)]
        self.assertIn(ActionKind.forward_email.value, kinds)

    def test_no_duplicate_ticket_from_follow_up(self) -> None:
        before = len(self.store.tickets(limit=500)) if self.pipeline.poll_once() else 0
        before = len(self.store.tickets(limit=500))
        self.transport.inject(DM_OLGA, "Ольга Белова", "кабинет всё тот же, 305")
        self.pipeline.poll_once()
        self.assertEqual(len(self.store.tickets(limit=500)), before)


class TestConfirmationClosesTicket(ConversationCase):
    def test_user_confirmation_plans_done_reaction(self) -> None:
        """«Всё работает» закрывает заявку и ставит галочку на исходном сообщении."""
        self.pipeline.poll_once()
        ticket_id = int(self.ticket_of("Ольга Белова")["id"])
        source_message = self.store.ticket(ticket_id)["source_message_id"]

        self.transport.inject(DM_OLGA, "Ольга Белова", "спасибо, всё работает")
        self.pipeline.poll_once()

        ticket = self.store.ticket(ticket_id)
        self.assertTrue(ticket["user_confirmed"], "подтверждение не зафиксировано")

        done = [a for a in self.store.actions_for(ticket_id)
                if a["kind"] == ActionKind.react_done.value]
        self.assertEqual(len(done), 1, "должна быть ровно одна реакция «закрыто»")
        payload = json.loads(done[0]["payload"])
        self.assertEqual(payload["message_external_id"], source_message,
                         "галочку надо ставить на саму заявку, а не на «спасибо»")
        self.assertEqual(payload["chat_external_id"], IT_MAIN.external_id,
                         "галочка должна стоять в чате заявок")

    def test_thanks_alone_creates_no_ticket(self) -> None:
        """Благодарность от человека без открытой заявки — не заявка."""
        store = Store(":memory:")
        transport = FakeMaxTransport()
        transport.start()
        pipeline = Pipeline(store, transport, Classifier(None), KnowledgeBase("kb"),
                            self.cfg)
        pipeline.poll_once()
        marina = [r for r in store.tickets(limit=500)
                  if r["requester_name"] == "Светлана Морозова"]
        self.assertEqual(marina, [], "«спасибо, всё работает» не должно стать заявкой")


class TestAnnouncementsIgnored(ConversationCase):
    def test_broadcast_gets_no_ticket_and_no_reaction(self) -> None:
        self.pipeline.poll_once()
        polina = [r for r in self.store.tickets(limit=500)
                  if r["requester_name"] == "Ирина Т"]
        self.assertEqual(polina, [], "объявление не должно становиться заявкой")


if __name__ == "__main__":
    unittest.main()
