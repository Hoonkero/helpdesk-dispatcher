"""Тесты правил, нарушение которых недопустимо.

Каждый тест здесь охраняет обещание, данное заказчику. Если такой тест
краснеет, изменение нельзя выпускать, даже если всё остальное работает.
"""
from __future__ import annotations

import unittest

from app.config import Mode, Settings
from app.core.classify import Classifier
from app.core.kb import KnowledgeBase
from app.core.pipeline import Pipeline
from app.core.policy import build_decision
from app.domain import ActionKind, ActionStatus, ChatInfo, IncomingMessage
from app.executors.runner import ActionRunner
from app.store import Store
from app.transports.base import WriteForbidden
from app.transports.fake import BOLTALKA, IT_MAIN, IT_BRANCH, FakeMaxTransport


def make(mode: Mode = Mode.shadow, *, safe: bool = True) -> tuple:
    cfg = Settings(max_watch_chats="ИТ Заявки", safe_mode=safe, llm_enabled=False,
                   dispatcher_mode=mode)
    store = Store(":memory:")
    transport = FakeMaxTransport()
    transport.start()
    pipeline = Pipeline(store, transport, Classifier(None), KnowledgeBase("kb"), cfg)
    runner = ActionRunner(store, transport, cfg)
    return cfg, store, transport, pipeline, runner


class TestNeverWritesToRequestChannel(unittest.TestCase):
    """Обещание 1: в чат заявок бот не пишет никогда, только реакции."""

    def test_transport_refuses_group_chat(self) -> None:
        transport = FakeMaxTransport()
        with self.assertRaises(WriteForbidden):
            transport.send_message(IT_MAIN, "любой текст")
        self.assertEqual(transport.sent, [], "сообщение не должно было уйти")

    def test_policy_never_plans_message_into_request_channel(self) -> None:
        """Ни для одной заявки план не содержит отправки в сам чат заявок."""
        kb = KnowledgeBase("kb")
        classifier = Classifier(None)
        transport = FakeMaxTransport()
        for msg in transport.fetch_new_messages(IT_MAIN):
            decision = build_decision(
                msg, classifier.classify(msg.text), kb,
                is_request_channel=True, escalation_contact="Вторая линия поддержки")
            for action in decision.actions:
                if action.kind == ActionKind.send_dm:
                    self.assertNotEqual(
                        action.payload.get("chat_external_id"), IT_MAIN.external_id,
                        f"план пытается писать в чат заявок: {msg.text!r}")

    def test_runner_refuses_to_send_into_group(self) -> None:
        _, store, transport, _, runner = make(safe=False)
        chat_id = store.upsert_chat(IT_MAIN, is_request_channel=True)
        msg = IncomingMessage(chat=IT_MAIN, external_id="m1", author_name="Кто-то",
                              text="тест")
        from app.domain import Category, Decision, Resolution
        decision = Decision(Category.printer, Resolution.need_clarify, 0.9, "rules",
                            "т", "т")
        ticket = store.create_ticket(chat_id=chat_id, chat_title=IT_MAIN.title,
                                     msg=msg, decision=decision)
        action = store.add_action(
            ticket, ActionKind.send_dm,
            {"chat_external_id": IT_MAIN.external_id, "to_name": "ИТ Заявки", "text": "нельзя"},
            "намеренно неверное действие", status=ActionStatus.approved)
        result = runner.execute(action)
        self.assertFalse(result.ok)
        self.assertEqual(transport.sent, [], "в групповой чат ничего не ушло")


class TestSafeMode(unittest.TestCase):
    """Обещание 2: пока предохранитель включён, наружу не уходит ничего."""

    def test_nothing_leaves_while_safe_mode_on(self) -> None:
        _, store, transport, pipeline, runner = make(Mode.auto, safe=True)
        pipeline.poll_once()
        # В авторежиме все действия одобрены — но выполниться не должны.
        results = runner.run_pending(limit=100)
        self.assertTrue(results, "действия должны были быть в очереди")

        # Записи в журнал выполняются всегда: у них нет внешнего эффекта.
        # А вот всё, что уходит людям, обязано быть остановлено.
        kinds = {int(r["id"]): r["kind"]
                 for r in store._all("SELECT id, kind FROM actions")}
        external = [(aid, res) for aid, res in results if kinds[aid] != "note"]
        self.assertTrue(external, "внешние действия должны были быть запланированы")
        for aid, res in external:
            self.assertTrue(res.dry_run,
                            f"действие {kinds[aid]} прорвалось через предохранитель")
        self.assertEqual(transport.reactions, [], "реакций быть не должно")
        self.assertEqual(transport.sent, [], "сообщений быть не должно")

    def test_actions_marked_skipped_not_done(self) -> None:
        """Пропущенное действие нельзя помечать выполненным — иначе его потеряют."""
        _, store, _, pipeline, runner = make(Mode.auto, safe=True)
        pipeline.poll_once()
        runner.run_pending(limit=100)
        rows = store._all("SELECT kind, status FROM actions WHERE kind != 'note'")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["status"], ActionStatus.skipped.value,
                             f"{row['kind']} должно быть пропущено, а не выполнено")


class TestMutedChats(unittest.TestCase):
    """Обещание 3: приглушённый чат не рабочий и не читается вообще."""

    def test_muted_chats_are_not_read(self) -> None:
        _, store, _, pipeline, _ = make()
        summary = pipeline.poll_once()
        self.assertEqual(summary["skipped_muted"], 2)
        titles = {row["chat_title"] for row in store.tickets(limit=200)}
        self.assertNotIn(IT_BRANCH.title, titles)
        self.assertNotIn(BOLTALKA.title, titles)

    def test_no_messages_stored_from_muted(self) -> None:
        _, store, _, pipeline, _ = make()
        pipeline.poll_once()
        rows = store._all(
            "SELECT c.title FROM messages m JOIN chats c ON c.id = m.chat_id")
        self.assertNotIn(IT_BRANCH.title, {r["title"] for r in rows})


class TestShadowMode(unittest.TestCase):
    """Обещание 4: в режиме shadow каждое внешнее действие ждёт оператора."""

    def test_everything_waits_for_approval(self) -> None:
        _, store, _, pipeline, _ = make(Mode.shadow)
        pipeline.poll_once()
        external = store._all(
            "SELECT kind, status FROM actions WHERE kind != 'note'")
        self.assertTrue(external)
        for row in external:
            self.assertEqual(row["status"], ActionStatus.proposed.value,
                             f"{row['kind']} не должно исполняться само")

    def test_assisted_mode_auto_approves_only_reactions(self) -> None:
        _, store, _, pipeline, _ = make(Mode.assisted)
        pipeline.poll_once()
        for row in store._all("SELECT kind, status FROM actions"):
            if row["kind"] in ("react_seen", "note"):
                self.assertEqual(row["status"], ActionStatus.approved.value)
            else:
                self.assertEqual(row["status"], ActionStatus.proposed.value,
                                 f"{row['kind']} нельзя одобрять автоматически")


class TestIdempotence(unittest.TestCase):
    """Повторный опрос не должен плодить заявки и действия."""

    def test_second_poll_changes_nothing(self) -> None:
        _, store, _, pipeline, _ = make()
        first = pipeline.poll_once()
        before = store.stats()
        second = pipeline.poll_once()
        self.assertEqual(second["new_messages"], 0)
        self.assertEqual(second["tickets"], 0)
        self.assertEqual(store.stats(), before)
        self.assertGreater(first["tickets"], 0)


if __name__ == "__main__":
    unittest.main()
